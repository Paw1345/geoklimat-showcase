from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import DataChannel, RegistryEntry
from ingestion.pig_spatial import PROFILES, attribution, channel_url, config, slug
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare three disabled PIG polygon scans; dry-run default, existing channels untouched."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            for key, p in PROFILES.items():
                entry = RegistryEntry.objects.filter(registry_id=p["registry"]).first()
                if not entry:
                    raise CommandError("Import the registry first")
                existing = DataChannel.objects.filter(slug=slug(key)).first()
                if existing:
                    if (
                        existing.kind,
                        existing.url,
                        existing.region,
                        existing.config,
                        existing.registry_id,
                    ) != ("geojson", channel_url(key), "PL", config(key), entry.pk):
                        raise CommandError("Preserving administrator spatial configuration")
                    self.stdout.write(f"{slug(key)}: unchanged")
                    continue
                self.stdout.write(f"{slug(key)}: prepare disabled pinned scan")
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=entry,
                        slug=slug(key),
                        name="PIG — " + p["dataset"] + " (przebieg)",
                        kind="geojson",
                        url=channel_url(key),
                        region="PL",
                        config=config(key),
                        attribution=attribution(key),
                        enabled=False,
                        access_approved=False,
                        interval_minutes=1440,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
