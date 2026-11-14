from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.bdl_paging import (
    ATTRIBUTION,
    INTERVAL_MINUTES,
    PROFILES,
    channel_url,
    config,
    slug,
)
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


def name(collection):
    if collection.endswith("_wydzielenia"):
        region = collection.removeprefix("RDLP_").removesuffix("_wydzielenia")
        return "BDL — wydzielenia RDLP " + region.replace("_", " ") + " (atrybuty, przebieg)"
    return "BDL — " + collection + " (geometria, przebieg)"


class Command(BaseCommand):
    help = "Prepare disabled BDL paged scans; dry-run default, no HTTP, existing kept."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; no configuration changes")
            registry = RegistryEntry.objects.filter(registry_id=39).first()
            if not registry:
                raise CommandError("Import the registry first")
            for collection in PROFILES:
                expected = (registry.pk, "geojson", "PL", channel_url(collection))
                existing = DataChannel.objects.filter(slug=slug(collection)).first()
                if existing:
                    actual = (existing.registry_id, existing.kind, existing.region, existing.url)
                    if actual != expected:
                        raise CommandError("Preserving administrator BDL scan configuration")
                    if existing.config != config(collection):
                        # Only a reviewed page-size change on a never-run, disabled scan.
                        if (
                            {
                                **existing.config,
                                "feature_limit": config(collection)["feature_limit"],
                            }
                            != config(collection)
                            or existing.enabled
                            or existing.access_approved
                            or existing.harvests.exists()
                            or existing.snapshots.exists()
                        ):
                            raise CommandError("Preserving administrator BDL scan configuration")
                        self.stdout.write(f"{slug(collection)}: update page size, stays disabled")
                        if options["apply"]:
                            existing.config = config(collection)
                            existing.last_status, existing.last_error = "needs_audit", ""
                            existing.save(update_fields=["config", "last_status", "last_error"])
                        continue
                    self.stdout.write(f"{slug(collection)}: unchanged")
                    continue
                self.stdout.write(f"{slug(collection)}: prepare disabled scan")
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=registry,
                        slug=slug(collection),
                        name=name(collection),
                        kind="geojson",
                        url=channel_url(collection),
                        region="PL",
                        config=config(collection),
                        attribution=ATTRIBUTION,
                        interval_minutes=INTERVAL_MINUTES,
                        enabled=False,
                        access_approved=False,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
