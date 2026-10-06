"""Prepare a directory-only channel without closing the full JRC connector gap."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.jrc_catalogue import CONFIG, NOTE, SLUG, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure JRC ENVI metadata only; dry-run default, no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=181).first()
            if entry is None:
                raise CommandError("Import the registry first")
            source = Source.objects.filter(slug=SLUG).first()
            if source:
                if (source.adapter, source.url, source.config) != ("json", URL, CONFIG) or not (
                    entry.sources.filter(pk=source.pk).exists()
                ):
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("Unchanged; audit and activation preserved")
                return
            if entry.sources.exists() or entry.integration_state != "needs_connector":
                raise CommandError("Preserving existing registry configuration")
            self.stdout.write(f"{SLUG}: create disabled metadata channel; full gap remains")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            source = Source.objects.create(
                slug=SLUG,
                name="JRC — katalog metadanych środowiskowych ENVI",
                organization=entry.organization[:120],
                adapter="json",
                url=URL,
                config=CONFIG.copy(),
                language="en",
                priority=int(entry.priority[1]),
                interval_minutes=360,
                scope=NOTE,
                reuse_notes=entry.technical.get("Reuse / licence") or "",
                enabled=False,
                activation_approved=False,
            )
            entry.sources.add(source)
            entry.integration_note = NOTE + " Pełny konektor danych nadal needs_connector."
            entry.save(update_fields=["integration_note"])
