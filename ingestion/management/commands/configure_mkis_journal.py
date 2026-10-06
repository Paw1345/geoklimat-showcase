from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.mkis_journal import CONFIG, LEGACY, NOTE, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Upgrade only untouched disabled MKIS journal; dry-run/no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=4).first()
            source = Source.objects.filter(slug="registry-004").first()
            if (
                not entry
                or not source
                or entry.integration_state != "configured"
                or list(entry.sources.values_list("pk", flat=True)) != [source.pk]
                or list(source.registry_entries.values_list("registry_id", flat=True)) != [4]
            ):
                raise CommandError("Preserving administrator registry linkage/state")
            if (source.adapter, source.url, source.config) == ("web", URL, CONFIG):
                self.stdout.write("registry-004: unchanged; audit/approval preserved")
                return
            if (
                (source.adapter, source.url, source.config) != ("web", LEGACY, {})
                or source.enabled
                or source.activation_approved
                or source.items.exists()
                or source.runs.exists()
                or source.last_success_at is not None
                or source.failures
                or source.interval_minutes != 120
                or source.scope
                != (
                    "Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
                    "Bez pełnego archiwum."
                )
            ):
                raise CommandError("Preserving administrator configuration/history")
            self.stdout.write("registry-004: replace unused endpoint with disabled journal GET")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            source.url, source.config, source.scope = URL, CONFIG, NOTE
            source.last_audit, source.audited_at = {}, None
            source.etag = source.last_modified = ""
            source.save(
                update_fields=[
                    "url",
                    "config",
                    "scope",
                    "last_audit",
                    "audited_at",
                    "etag",
                    "last_modified",
                ]
            )
