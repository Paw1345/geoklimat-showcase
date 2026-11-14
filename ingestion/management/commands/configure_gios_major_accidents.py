from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.gios_major_accidents import ATTRIBUTION, CONFIG, SLUG, URL
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure disabled GIOS major-accident register tables (registry 50). No HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=50).first()
            if not entry:
                raise CommandError("Import the registry first")
            existing = DataChannel.objects.filter(slug=SLUG).first()
            if existing:
                if (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                    existing.attribution,
                ) != (entry.pk, "table", URL, CONFIG, "PL", ATTRIBUTION):
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("Unchanged; audit, snapshots and activation preserved")
                return
            if options["apply"]:
                DataChannel.objects.create(
                    registry=entry,
                    slug=SLUG,
                    name="GIOŚ — poważne awarie 2022–2024 i rejestry ZDR/ZZR (pliki dane.gov.pl)",
                    kind="table",
                    url=URL,
                    config=CONFIG.copy(),
                    region="PL",
                    attribution=ATTRIBUTION,
                    interval_minutes=360,
                )
            # Registry state stays needs_connector: the new API remains blocked.
            self.stdout.write(f"Create disabled {SLUG}; applied={options['apply']}")
