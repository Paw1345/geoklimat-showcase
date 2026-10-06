from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import DataChannel, RegistryEntry
from ingestion.pig_data import ATTRIBUTION, LABELS, channel_url, config, slug
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare three disabled bounded MIDAS GeoJSON channels; dry-run default, no HTTP."

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
                channel = DataChannel.objects.filter(slug=slug(layer)).first()
                if channel:
                    if (
                        channel.kind,
                        channel.url,
                        channel.region,
                        channel.config,
                        channel.registry_id,
                    ) != ("geojson", channel_url(layer), "PL", config(layer), entry.pk):
                        raise CommandError("Preserving administrator channel configuration")
                    self.stdout.write(f"{slug(layer)}: unchanged; audit/activation preserved")
                    continue
                self.stdout.write(f"{slug(layer)}: create disabled100-feature sample")
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=entry,
                        slug=slug(layer),
                        name="PIG MIDAS — " + label + " (próbka100)",
                        kind="geojson",
                        url=channel_url(layer),
                        region="PL",
                        config=config(layer),
                        attribution=ATTRIBUTION,
                        interval_minutes=1440,
                        enabled=False,
                        access_approved=False,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
