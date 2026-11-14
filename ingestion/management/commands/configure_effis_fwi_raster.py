from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.effis_fwi_raster import ATTRIBUTION, BASE, CONFIG, NAME, REGISTRY_ID, SLUG
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock

# CEMS attribution names the year of the information; the profile is reviewed per year.
YEAR = 2026


class Command(BaseCommand):
    help = (
        "Configure the disabled daily EFFIS FWI GeoTIFF channel for Poland (202). Dry-run default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=REGISTRY_ID).first()
            if not entry:
                raise CommandError("Import the registry first")
            attribution = ATTRIBUTION.format(year=YEAR)
            existing = DataChannel.objects.filter(slug=SLUG).first()
            if existing:
                if (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                ) != (entry.pk, "table", BASE, CONFIG, "PL"):
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write(f"{SLUG}: unchanged; audit and activation kept")
                return
            self.stdout.write(f"{SLUG}: create disabled; applied={options['apply']}")
            if options["apply"]:
                DataChannel.objects.create(
                    registry=entry,
                    slug=SLUG,
                    name=NAME,
                    kind="table",
                    url=BASE,
                    config=CONFIG,
                    region="PL",
                    attribution=attribution,
                    enabled=False,
                    access_approved=False,
                    # Daily product; one GetMap per day, identical rasters dedupe.
                    interval_minutes=1440,
                )
            else:
                self.stdout.write("Dry-run; no writes or HTTP")
