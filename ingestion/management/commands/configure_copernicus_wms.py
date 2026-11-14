"""Prepare disabled EDO/EFFIS WMS layer catalogue channels (4.5.7)."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.copernicus_wms import NOTE, PROFILES, config, slug
from ingestion.models import RegistryEntry, Source, Topic
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure reviewed EDO/EFFIS WMS catalogues; dry-run default, no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            ids = {profile["registry_id"] for profile in PROFILES.values()}
            entries = {
                e.registry_id: e
                for e in RegistryEntry.objects.select_for_update().filter(registry_id__in=ids)
            }
            if set(entries) != ids:
                raise CommandError("Import the registry first")
            # Validate every profile before writing any; preserve administrator edits.
            existing = {}
            for key, profile in PROFILES.items():
                entry = entries[profile["registry_id"]]
                source = Source.objects.filter(slug=slug(key)).first()
                if source:
                    if (source.adapter, source.url, source.config) != (
                        "ogc",
                        profile["url"],
                        config(key),
                    ) or set(source.registry_entries.values_list("registry_id", flat=True)) != {
                        profile["registry_id"]
                    }:
                        raise CommandError("Preserving administrator configuration or linkage")
                elif entry.integration_state != "needs_connector" or entry.sources.exists():
                    raise CommandError("Preserving existing registry state or sources")
                existing[key] = source
            for key, profile in PROFILES.items():
                if existing[key]:
                    self.stdout.write(f"{slug(key)}: unchanged; audit/activation preserved")
                    continue
                self.stdout.write(f"{slug(key)}: create disabled WMS layer catalogue")
                if not options["apply"]:
                    continue
                source = Source.objects.create(
                    slug=slug(key),
                    name=profile["name"],
                    organization=profile["organization"],
                    adapter="ogc",
                    url=profile["url"],
                    config=config(key),
                    language="en",
                    priority=1,
                    interval_minutes=1440,
                    scope=NOTE,
                    reuse_notes=(
                        "Tylko metadane usługi; warunki danych i atrybucja: " + profile["terms"]
                    ),
                    enabled=False,
                    activation_approved=False,
                )
                source.topics.set(
                    Topic.objects.filter(
                        slug__in=["climate", "open_data_api", "spatial_information"]
                    )
                )
                entry = entries[profile["registry_id"]]
                entry.sources.add(source)
                entry.integration_note = NOTE
                entry.save(update_fields=["integration_note"])
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
