from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.marine import CONFIG, NOTE, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure public Marine STAC product metadata; dry-run default, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=195).first()
            if entry is None:
                raise CommandError("Import the registry first")
            source = Source.objects.filter(slug="registry-195").first()
            if source:
                if (source.adapter, source.url, source.config) != (
                    "stac",
                    URL,
                    CONFIG,
                ) or not entry.sources.filter(pk=source.pk).exists():
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("Unchanged; audit and activation preserved")
                return
            if entry.sources.exists():
                raise CommandError("Preserving existing registry linkage")
            self.stdout.write("registry-195: create disabled Marine product metadata channel")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            source = Source.objects.create(
                slug="registry-195",
                name="Copernicus Marine — katalog produktów",
                organization=entry.organization[:120],
                adapter="stac",
                url=URL,
                config=CONFIG.copy(),
                language="en",
                priority=int(entry.priority[1]),
                interval_minutes=1440,
                scope=NOTE,
                reuse_notes=entry.technical.get("Reuse / licence") or "",
                enabled=False,
                activation_approved=False,
            )
            entry.sources.add(source)
            entry.integration_state, entry.integration_note = "configured", NOTE
            entry.save(update_fields=["integration_state", "integration_note"])
