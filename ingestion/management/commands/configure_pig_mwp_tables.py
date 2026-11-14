from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import DataChannel, RegistryEntry
from ingestion.pig_mwp_tables import ATTRIBUTION, REGISTRY_ID, TABLES, channel_url, config, slug
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure disabled MWP measurement/chemistry tables (registry 63). Dry-run default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=REGISTRY_ID).first()
            if not entry:
                raise CommandError("Import the registry first")
            plan = []
            for key in TABLES:
                existing = DataChannel.objects.filter(slug=slug(key)).first()
                if existing and (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                ) != (entry.pk, "table", channel_url(key), config(key), "PL"):
                    raise CommandError("Preserving administrator configuration or linkage")
                plan.append((key, existing))
            for key, existing in plan:
                if existing:
                    self.stdout.write(f"{slug(key)}: unchanged; audit and activation kept")
                    continue
                self.stdout.write(f"{slug(key)}: create disabled; applied={options['apply']}")
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=entry,
                        slug=slug(key),
                        name="PIG — " + TABLES[key]["title"],
                        kind="table",
                        url=channel_url(key),
                        config=config(key),
                        region="PL",
                        attribution=ATTRIBUTION,
                        enabled=False,
                        access_approved=False,
                        # The provider window moves monthly/yearly; identical copies dedupe.
                        interval_minutes=1440,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
