from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.cellar import CONFIG, NOTE, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure reviewed Cellar metadata scope; dry-run default, no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=170).first()
            if entry is None:
                raise CommandError("Import the registry first")
            existing = Source.objects.filter(slug="registry-170").first()
            if existing:
                if (existing.adapter, existing.url, existing.config) != (
                    "json",
                    URL,
                    CONFIG,
                ) or not entry.sources.filter(pk=existing.pk).exists():
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("Unchanged; audit and activation preserved")
                return
            if entry.sources.exists():
                raise CommandError("Preserving existing registry linkage")
            self.stdout.write("registry-170: create disabled Cellar environmental metadata channel")
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
                return
            source = Source.objects.create(
                slug="registry-170",
                name="Cellar — environmental policy: metadane dokumentów",
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
            entry.integration_state, entry.integration_note = "configured", NOTE
            entry.save(update_fields=["integration_state", "integration_note"])
