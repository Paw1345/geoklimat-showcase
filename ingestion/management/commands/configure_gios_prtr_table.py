from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.gios_prtr_table import profiles, validate_profile
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Configure a disabled PRTR annual releases table (registry 49). No HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--year", type=int, choices=sorted(profiles()), default=2017)

    def handle(self, *args, **options):
        year = options["year"]
        SLUG, URL, CONFIG, ATTRIBUTION, _ = profiles()[year]
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=49).first()
            if not entry:
                raise CommandError("Import the registry first")
            existing = DataChannel.objects.filter(slug=SLUG).first()
            if existing:
                try:
                    validate_profile(existing)
                except FetchError as exc:
                    raise CommandError("Preserving administrator configuration or linkage") from exc
                self.stdout.write("Unchanged; audit, snapshots and activation preserved")
                return
            if options["apply"]:
                DataChannel.objects.create(
                    registry=entry,
                    slug=SLUG,
                    name=f"GIOŚ — PRTR: uwolnienia za {year} (Polska)",
                    kind="table",
                    url=URL,
                    config=CONFIG.copy(),
                    region="PL",
                    attribution=ATTRIBUTION,
                    interval_minutes=1440,
                )
            self.stdout.write(f"Create disabled {SLUG}; applied={options['apply']}")
