from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.catalogue_profiles import CONFIG, NOTE, PROFILES
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure public CDS/ADS metadata catalogues; default dry-run, no credentials or HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entries = {
                e.registry_id: e for e in RegistryEntry.objects.filter(registry_id__in=PROFILES)
            }
            if set(entries) != set(PROFILES):
                raise CommandError("Import the registry first")
            for key, url in PROFILES.items():
                entry, slug = entries[key], f"registry-{key:03d}"
                existing = Source.objects.filter(slug=slug).first()
                if existing:
                    if (existing.adapter, existing.url, existing.config) != (
                        "stac",
                        url,
                        CONFIG,
                    ) or not entry.sources.filter(pk=existing.pk).exists():
                        raise CommandError("Preserving administrator configuration or linkage")
                    self.stdout.write(f"{slug}: unchanged; audit and activation preserved")
                    continue
                if entry.sources.exists():
                    raise CommandError("Preserving existing registry linkage")
                self.stdout.write(f"{slug}: create disabled public metadata catalogue")
                if not options["apply"]:
                    continue
                source = Source.objects.create(
                    slug=slug,
                    name=f"{entry.organization} — katalog kolekcji"[:160],
                    organization=entry.organization[:120],
                    adapter="stac",
                    url=url,
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
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
