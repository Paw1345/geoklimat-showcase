from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.gios_stations import CONFIG, LEGACY_CONFIG, NOTE
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock
from ingestion.transport import GIOS_STATIONS_URL


class Command(BaseCommand):
    help = "Configure user-approved GIOS station exception only; dry-run/no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=7).first()
            source = Source.objects.filter(slug="registry-007").first()
            if (
                not entry
                or not source
                or entry.integration_state != "configured"
                or list(entry.sources.values_list("pk", flat=True)) != [source.pk]
                or list(source.registry_entries.values_list("registry_id", flat=True)) != [7]
            ):
                raise CommandError("Preserving administrator registry linkage/state")
            if (source.adapter, source.url, source.config) == ("json", GIOS_STATIONS_URL, CONFIG):
                self.stdout.write("registry-007: unchanged; audit/approval preserved")
                return
            if (
                (source.adapter, source.url, source.config)
                != ("json", GIOS_STATIONS_URL, LEGACY_CONFIG)
                or source.enabled
                or source.activation_approved
                or source.items.exists()
                or source.runs.exists()
                or source.last_success_at is not None
                or source.failures
                or source.interval_minutes != 30
                or source.scope != "Lista stacji v1; nie pomiary ani alerty zdrowotne."
            ):
                raise CommandError("Preserving administrator configuration/history")
            self.stdout.write("registry-007: configure disabled approved station API profile,60min")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            source.config, source.scope, source.interval_minutes = CONFIG, NOTE, 60
            source.reuse_notes += (
                " Źródło danych: GIOŚ - EKOINFONET. Lista aktywnych stacji; "
                "bez pomiarów. Warunki API: https://powietrze.gios.gov.pl/pjp/content/terms_of_service"
            )
            source.last_audit, source.audited_at = {}, None
            source.etag = source.last_modified = ""
            source.save(
                update_fields=[
                    "config",
                    "scope",
                    "interval_minutes",
                    "reuse_notes",
                    "last_audit",
                    "audited_at",
                    "etag",
                    "last_modified",
                ]
            )
