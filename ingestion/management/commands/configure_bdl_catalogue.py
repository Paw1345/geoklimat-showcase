"""Prepare a disabled BDL metadata source, preserving business coverage gaps."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.bdl_catalogue import CONFIG, NOTE, SLUG, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure BDL collection metadata; dry-run default, no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            row = RegistryEntry.objects.select_for_update().filter(registry_id=39).first()
            if not row or row.integration_state != "needs_connector":
                raise CommandError("Missing registry39 or preserving registry state")
            source = Source.objects.select_for_update().filter(slug=SLUG).first()
            if source:
                if (source.adapter, source.url, source.config) != ("json", URL, CONFIG) or set(
                    source.registry_entries.values_list("registry_id", flat=True)
                ) != {39}:
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("BDL unchanged; audit, activation and history preserved")
                return
            self.stdout.write("BDL: create disabled metadata source; full connector stays open")
            if options["apply"]:
                source = Source.objects.create(
                    slug=SLUG,
                    name="Bank Danych o Lasach — katalog kolekcji OGC API",
                    organization="Bank Danych o Lasach",
                    adapter="json",
                    url=URL,
                    config=CONFIG,
                    interval_minutes=360,
                    scope=NOTE,
                    reuse_notes="Brak potwierdzonej zgody na redystrybucję danych przestrzennych.",
                    enabled=False,
                    activation_approved=False,
                )
                row.sources.add(source)
                row.integration_note = NOTE
                row.save(update_fields=["integration_note"])
            else:
                self.stdout.write("Dry-run; no writes or HTTP")
