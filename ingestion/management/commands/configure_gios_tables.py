from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.gios_tables import RESOURCES, profile
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock

NAMES = {
    "groundwater": "GIOŚ — wody podziemne, wyniki fizykochemiczne {} (pliki dane.gov.pl)",
    "noise_road": "GIOŚ — hałas drogowy, wyniki pomiarów {} (plik dane.gov.pl)",
}


class Command(BaseCommand):
    help = "Configure disabled GIOS yearly result tables (registries 47/48). No HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entries = {
                entry.registry_id: entry
                for entry in RegistryEntry.objects.filter(registry_id__in=[47, 48])
            }
            if set(entries) != {47, 48}:
                raise CommandError("Import the registry first")
            # Validate the whole package before the first write.
            plan = []
            for product, year in sorted(RESOURCES):
                expected = profile(product, year)
                entry = entries[expected["registry_id"]]
                existing = DataChannel.objects.filter(slug=expected["slug"]).first()
                if existing and (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                    existing.attribution,
                ) != (
                    entry.pk,
                    "table",
                    expected["url"],
                    expected["config"],
                    "PL",
                    expected["attribution"],
                ):
                    raise CommandError("Preserving administrator configuration or linkage")
                plan.append((product, year, expected, entry, existing))
            for product, year, expected, entry, existing in plan:
                if existing:
                    self.stdout.write(f"{expected['slug']}: unchanged; audit and activation kept")
                    continue
                self.stdout.write(
                    f"{expected['slug']}: create disabled; applied={options['apply']}"
                )
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=entry,
                        slug=expected["slug"],
                        name=NAMES[product].format(year),
                        kind="table",
                        url=expected["url"],
                        config=expected["config"],
                        region="PL",
                        attribution=expected["attribution"],
                        # Annual files: daily checks are enough and avoid needless downloads.
                        interval_minutes=1440,
                    )
            # Registry states stay needs_connector: the new API remains blocked.
