from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.imgw_daily import ATTRIBUTION, CONFIG, ROOT
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure a disabled IMGW daily backfill profile. Dry-run default, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=34).first()
            if not entry:
                raise CommandError("Import the registry first")
            slug = "imgw-synop-daily"
            existing = DataChannel.objects.filter(slug=slug).first()
            if existing:
                if (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                ) != (entry.pk, "imgw_daily", ROOT, CONFIG, "PL"):
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("Unchanged; audit, history and activation preserved")
                return
            if options["apply"]:
                DataChannel.objects.create(
                    registry=entry,
                    slug=slug,
                    name="IMGW — archiwum dobowe SYNOP (opublikowana część ostatnich 30 dni)",
                    kind="imgw_daily",
                    url=ROOT,
                    config=CONFIG.copy(),
                    region="PL",
                    attribution=ATTRIBUTION,
                    interval_minutes=1440,
                )
            self.stdout.write(f"Create disabled {slug}; applied={options['apply']}")
