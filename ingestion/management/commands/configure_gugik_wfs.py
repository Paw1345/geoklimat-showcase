"""Prepare disabled GUGiK WFS catalogue sources; registry state is left unchanged."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.gugik_wfs import NOTE, SERVICES, config, slug, url
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure GUGiK WFS GetCapabilities sources; dry-run default, no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=53).first()
            if not entry:
                raise CommandError("Import the registry first")
            existing = {}
            for key in SERVICES:
                source = Source.objects.filter(slug=slug(key)).first()
                if source and (
                    (source.adapter, source.url, source.config) != ("ogc", url(key), config(key))
                    or not source.registry_entries.filter(pk=entry.pk).exists()
                ):
                    raise CommandError("Preserving administrator configuration or linkage")
                existing[key] = source
            for key, (title, _) in SERVICES.items():
                if existing[key]:
                    self.stdout.write(f"{slug(key)}: unchanged; audit/activation preserved")
                    continue
                self.stdout.write(f"{slug(key)}: create disabled WFS catalogue source")
                if options["apply"]:
                    source = Source.objects.create(
                        slug=slug(key),
                        name="GUGiK WFS — " + title,
                        organization="GUGiK",
                        adapter="ogc",
                        url=url(key),
                        config=config(key),
                        language="pl",
                        priority=1,
                        interval_minutes=360,
                        scope=NOTE,
                        reuse_notes=(
                            "Warunki GUGiK: wskazanie źródła i czasu pozyskania; warunki "
                            "zbioru w AccessConstraints. Opis usługi, nie kopia danych."
                        ),
                        enabled=False,
                        activation_approved=False,
                    )
                    entry.sources.add(source)
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
