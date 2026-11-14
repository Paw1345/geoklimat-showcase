from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.imgw_warnings import ATTRIBUTION, NAMES, PRODUCTS, config, slug, url
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure the disabled IMGW meteo/hydro warning channels (34, 33). Dry-run default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            for product, registry_id in PRODUCTS.items():
                entry = RegistryEntry.objects.filter(registry_id=registry_id).first()
                if not entry:
                    raise CommandError("Import the registry first")
                existing = DataChannel.objects.filter(slug=slug(product)).first()
                if existing:
                    if (
                        existing.registry_id,
                        existing.kind,
                        existing.url,
                        existing.config,
                        existing.region,
                    ) != (entry.pk, "table", url(product), config(product), "PL"):
                        raise CommandError("Preserving administrator configuration or linkage")
                    self.stdout.write(f"{slug(product)}: unchanged; audit and activation kept")
                    continue
                self.stdout.write(f"{slug(product)}: create disabled; applied={options['apply']}")
                if options["apply"]:
                    DataChannel.objects.create(
                        registry=entry,
                        slug=slug(product),
                        name=NAMES[product],
                        kind="table",
                        url=url(product),
                        config=config(product),
                        region="PL",
                        attribution=ATTRIBUTION,
                        enabled=False,
                        access_approved=False,
                        # Workbook asks for 30 min; identical responses dedupe by SHA-256.
                        interval_minutes=30,
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
