from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.ems_catalogue import CONFIG, NOTE, SLUG, TERMS, URL, validate_profile
from ingestion.models import RegistryEntry, Source, Topic
from ingestion.service import batch_lock
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Prepare disabled EMS initial EU metadata; dry-run default, no HTTP or terms acceptance."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.select_for_update().filter(registry_id=196).first()
            if not registry or registry.integration_state != "needs_connector":
                raise CommandError("Missing registry196 or preserving integration state")
            source = Source.objects.select_for_update().filter(slug=SLUG).first()
            if source:
                try:
                    validate_profile(source)
                except FetchError as exc:
                    raise CommandError("Preserving administrator configuration") from exc
                if set(source.registry_entries.values_list("registry_id", flat=True)) != {196}:
                    raise CommandError("Preserving administrator profile or linkage")
                self.stdout.write("EMS unchanged; approval, audit and history preserved")
                return
            if registry.sources.exists():
                raise CommandError("Preserving existing registry sources; no duplicate channel")
            self.stdout.write("EMS: prepare disabled first20 EU metadata; full196 remains open")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes, HTTP or acceptance of terms")
                return
            source = Source.objects.create(
                slug=SLUG,
                name="Copernicus EMS — aktywacje mapowania PL/UE (pierwsza strona)",
                organization="Copernicus EMS On-Demand Mapping",
                adapter="json",
                url=URL,
                config=CONFIG,
                language="en",
                interval_minutes=120,
                scope=NOTE,
                reuse_notes="Wymagane rozstrzygnięcie warunków i atrybucja: " + TERMS,
                enabled=False,
                activation_approved=False,
            )
            source.topics.set(
                Topic.objects.filter(slug__in=["water", "geoportal_geodesy", "open_data_api"])
            )
            registry.sources.add(source)
            registry.integration_note = NOTE
            registry.save(update_fields=["integration_note"])
