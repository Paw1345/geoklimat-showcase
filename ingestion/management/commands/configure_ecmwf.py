from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.ecmwf_catalogue import CONFIG, EVIDENCE, NOTE, SLUG, URL, validate_profile
from ingestion.models import RegistryEntry, Source, Topic
from ingestion.service import batch_lock
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Prepare public ECMWF collection metadata; dry-run default, no HTTP/activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.select_for_update().filter(registry_id=203).first()
            if not registry or registry.integration_state != "needs_connector":
                raise CommandError("Missing registry203 or preserving integration state")
            source = Source.objects.select_for_update().filter(slug=SLUG).first()
            if source:
                try:
                    validate_profile(source)
                except FetchError as exc:
                    raise CommandError("Preserving administrator configuration") from exc
                if set(source.registry_entries.values_list("registry_id", flat=True)) != {203}:
                    raise CommandError("Preserving administrator linkage")
                self.stdout.write("ECMWF unchanged; audit, approval and history preserved")
                return
            if registry.sources.exists():
                raise CommandError("Preserving existing sources; no duplicate channel")
            self.stdout.write("ECMWF: create disabled ECDS metadata source; full203 stays open")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes, HTTP, downloads or activation")
                return
            source = Source.objects.create(
                slug=SLUG,
                name="ECMWF — kolekcje ECDS (metadane, nie prognozy)",
                organization="ECMWF",
                adapter="stac",
                url=URL,
                config=CONFIG,
                interval_minutes=360,
                language="en",
                priority=2,
                enabled=False,
                activation_approved=False,
                scope=NOTE,
                reuse_notes=(
                    "Źródłowa licencja other, nie potwierdzona zgoda na pobieranie danych. "
                    "Pobrania wymagają osobnych uprawnień/warunków; nie akceptowano ich. "
                    + EVIDENCE
                ),
            )
            source.topics.set(
                Topic.objects.filter(slug__in=["climate", "open_data_api", "spatial_information"])
            )
            registry.sources.add(source)
            registry.integration_note = NOTE
            registry.save(update_fields=["integration_note"])
