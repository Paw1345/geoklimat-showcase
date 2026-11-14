from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.funding_calls import ATTRIBUTION, REGISTRY_ID, TITLES, config, slug
from ingestion.funding_search_transport import BASE, PROFILES
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure disabled daily LIFE/Horizon 5-6 funding call channels (189). Dry-run default."

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
            for key in PROFILES:
                existing = DataChannel.objects.filter(slug=slug(key)).first()
                if existing and (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                ) != (entry.pk, "table", BASE, config(key), "EU"):
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
                        name="KE F&T — " + TITLES[key],
                        kind="table",
                        url=BASE,
                        config=config(key),
                        region="EU",
                        attribution=ATTRIBUTION,
                        enabled=False,
                        access_approved=False,
                        # User card 2026-10-08T08:12Z: daily; identical state sets dedupe.
                        interval_minutes=1440,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
