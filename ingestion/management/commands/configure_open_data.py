from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.open_data import CONFIG, NOTE, URL
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure reviewed public dane.gov.pl metadata API; default dry-run, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=24).first()
            if entry is None:
                raise CommandError("Import the registry first")
            existing = Source.objects.filter(slug="registry-024").first()
            if existing:
                if (existing.adapter, existing.url, existing.config) != ("json", URL, CONFIG):
                    raise CommandError("Preserving administrator configuration; review required")
                self.stdout.write("Unchanged; audit and activation preserved")
                return
            if entry.sources.exists():
                raise CommandError("Preserving existing registry linkage")
            self.stdout.write("registry-024: create disabled public API metadata channel")
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
                return
            source = Source.objects.create(
                slug="registry-024",
                name="dane.gov.pl — zmiany zbiorów",
                organization="dane.gov.pl",
                adapter="json",
                url=URL,
                config=CONFIG.copy(),
                language="pl",
                priority=1,
                interval_minutes=360,
                scope=NOTE,
                reuse_notes=entry.technical.get("Reuse / licence") or "",
                enabled=False,
                activation_approved=False,
            )
            entry.sources.add(source)
            entry.integration_state, entry.integration_note = "configured", NOTE
            entry.save(update_fields=["integration_state", "integration_note"])
