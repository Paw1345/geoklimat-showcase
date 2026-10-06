from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.hys_catalogue import CONFIG, LEGACY, NOTE, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure untouched Have Your Say channel as ENV/CLIMA catalogue; dry-run/no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.filter(registry_id=171).first()
            source = Source.objects.filter(slug="registry-171").first()
            if (
                not registry
                or not source
                or registry.integration_state != "configured"
                or (
                    list(registry.sources.values_list("pk", flat=True)) != [source.pk]
                    or list(source.registry_entries.values_list("registry_id", flat=True)) != [171]
                )
            ):
                raise CommandError("Preserving administrator registry linkage/state")
            if (source.adapter, source.url, source.config) == ("json", URL, CONFIG):
                self.stdout.write("HYS: unchanged; audit/history/approval preserved")
                return
            if (source.adapter, source.url, source.config) != ("web", LEGACY, {}) or (
                source.enabled
                or source.activation_approved
                or source.items.exists()
                or source.runs.exists()
                or source.failures
                or source.last_success_at is not None
                or source.interval_minutes != 60
                or source.scope
                != (
                    "Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
                    "Bez pełnego archiwum."
                )
            ):
                raise CommandError("Preserving administrator source configuration/history")
            self.stdout.write(
                "HYS: replace unused JS shell with disabled public ENV/CLIMA catalogue"
            )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            source.adapter, source.url, source.config, source.scope = "json", URL, CONFIG, NOTE
            source.language = "mul"
            source.last_audit, source.audited_at = {}, None
            source.etag = source.last_modified = ""
            source.save(
                update_fields=[
                    "adapter",
                    "url",
                    "config",
                    "scope",
                    "language",
                    "last_audit",
                    "audited_at",
                    "etag",
                    "last_modified",
                ]
            )
