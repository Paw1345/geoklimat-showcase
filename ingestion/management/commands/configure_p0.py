from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.p0_profiles import PREVIOUS_URLS, PROFILES
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Review/apply six public P0 routes without altering workbook or administrator edits."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; no configuration changed")
            entries = {
                entry.registry_id: entry
                for entry in RegistryEntry.objects.filter(registry_id__in=PROFILES)
            }
            if set(entries) != set(PROFILES):
                raise CommandError("Import the registry first; required P0 entries missing")
            for key, (url, config, note) in PROFILES.items():
                entry = entries[key]
                slug = f"registry-{key:03d}"
                source = Source.objects.filter(slug=slug).first()
                if source and (source.adapter, source.url, source.config) == ("web", url, config):
                    self.stdout.write(f"{slug}: unchanged (audit/activation retained)")
                    continue
                if source and not (
                    source.adapter == "web"
                    and source.config == {}
                    and source.url == PREVIOUS_URLS.get(key)
                    and entry.sources.filter(pk=source.pk).exists()
                ):
                    self.stdout.write(f"{slug}: skipped administrator configuration")
                    continue
                self.stdout.write(
                    f"{slug}: {'update' if source else 'create'}; {url}; disabled pending audit"
                )
                if not options["apply"]:
                    continue
                if source is None:
                    source = Source(
                        slug=slug,
                        name=f"{entry.organization} — {entry.name}"[:160],
                        organization=entry.organization[:120],
                        language="pl" if entry.country == "Polska" else "en",
                        priority=int(entry.priority[1]),
                        interval_minutes=entry.master["Poll interval (min)"],
                        reuse_notes=entry.technical.get("Reuse / licence") or "",
                    )
                source.adapter, source.url, source.config = "web", url, config.copy()
                source.scope = note
                source.enabled = source.activation_approved = False
                source.last_audit, source.audited_at = {}, None
                source.etag = source.last_modified = ""
                source.save()
                entry.sources.add(source)
                entry.integration_state, entry.integration_note = "configured", note
                entry.save(update_fields=["integration_state", "integration_note"])
            if not options["apply"]:
                self.stdout.write("Dry run; no database writes or HTTP.")
