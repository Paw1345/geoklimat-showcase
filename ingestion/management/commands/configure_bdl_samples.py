from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.bdl_probe import COLLECTIONS, plan
from ingestion.bdl_samples import ATTRIBUTION, config, slug
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare three disabled BDL original-response samples; no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; no configuration changes")
            registry = RegistryEntry.objects.select_for_update().filter(registry_id=39).first()
            if not registry or registry.integration_state != "needs_connector":
                raise CommandError("Missing registry39 or preserving existing registry state")
            prepared = []
            for collection in COLLECTIONS:
                expected = {
                    "registry_id": registry.pk,
                    "kind": "geojson",
                    "region": "PL",
                    "url": plan(collection)["url"],
                    "config": config(collection),
                    "interval_minutes": 1440,
                    "attribution": ATTRIBUTION,
                }
                channel = (
                    DataChannel.objects.select_for_update().filter(slug=slug(collection)).first()
                )
                if channel and any(
                    getattr(channel, key) != value for key, value in expected.items()
                ):
                    raise CommandError("Preserving administrator profile; no changes")
                prepared.append((collection, expected, channel))
            for collection, expected, channel in prepared:
                self.stdout.write(
                    f"{slug(collection)}: {'unchanged' if channel else 'create disabled'}"
                )
                if options["apply"] and channel is None:
                    DataChannel.objects.create(
                        slug=slug(collection),
                        name="BDL — " + collection + " (oryginalna próbka1)",
                        enabled=False,
                        access_approved=False,
                        **expected,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
