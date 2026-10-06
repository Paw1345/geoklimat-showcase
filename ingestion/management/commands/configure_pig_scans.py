from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import DataChannel, RegistryEntry
from ingestion.pig_data import LABELS, channel_url
from ingestion.pig_paging import ATTRIBUTION, config, slug
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = (
        "Prepare three disabled resumable MIDAS scans; dry-run default, existing samples untouched."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=65).first()
            if not entry:
                raise CommandError("Import the registry first")
            for layer, label in LABELS.items():
                existing = DataChannel.objects.filter(slug=slug(layer)).first()
                if existing:
                    if (
                        existing.kind,
                        existing.url,
                        existing.region,
                        existing.config,
                        existing.registry_id,
                    ) != ("geojson", channel_url(layer), "PL", config(layer), entry.pk):
                        raise CommandError("Preserving administrator scan configuration")
                    self.stdout.write(f"{slug(layer)}: unchanged")
                    continue
                self.stdout.write(f"{slug(layer)}: prepare disabled identity-pinned scan")
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=entry,
                        slug=slug(layer),
                        name="PIG MIDAS — " + label + " (przebieg)",
                        kind="geojson",
                        url=channel_url(layer),
                        region="PL",
                        config=config(layer),
                        attribution=ATTRIBUTION,
                        enabled=False,
                        access_approved=False,
                        interval_minutes=1440,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
