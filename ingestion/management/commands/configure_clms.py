from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.clms_catalogue import CONFIG, NOTE, POLICY, SLUG, URL, validate_profile
from ingestion.models import RegistryEntry, Source, Topic
from ingestion.service import batch_lock
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Prepare scoped CLMS/CDSE collection metadata; dry-run default, no HTTP/activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.select_for_update().filter(registry_id=192).first()
            if not registry or registry.integration_state != "needs_connector":
                raise CommandError("Missing registry192 or preserving integration state")
            source = Source.objects.select_for_update().filter(slug=SLUG).first()
            if source:
                try:
                    validate_profile(source)
                except FetchError as exc:
                    raise CommandError("Preserving administrator configuration") from exc
                if set(source.registry_entries.values_list("registry_id", flat=True)) != {192}:
                    raise CommandError("Preserving administrator linkage")
                self.stdout.write("CLMS unchanged; audit, approval and history preserved")
                return
            if registry.sources.exists():
                raise CommandError("Preserving existing registry sources; no duplicate channel")
            self.stdout.write(
                "CLMS: create disabled scoped CDSE metadata source; full192 stays open"
            )
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes, HTTP, downloads or activation")
                return
            source = Source.objects.create(
                slug=SLUG,
                name="CLMS — kolekcje CDSE (metadane, nie rastry)",
                organization="Copernicus Land Monitoring Service",
                adapter="stac",
                url=URL,
                config=CONFIG,
                interval_minutes=360,
                language="en",
                priority=1,
                enabled=False,
                activation_approved=False,
                scope=NOTE,
                reuse_notes=(
                    "Atrybucja CLMS/CDSE; zachowujemy wskaźnik licencji kolekcji, "
                    "nie udzielamy zgody na pobieranie lub redystrybucję jej plików. " + POLICY
                ),
            )
            source.topics.set(
                Topic.objects.filter(slug__in=["gis", "spatial_information", "open_data_api"])
            )
            registry.sources.add(source)
            registry.integration_note = NOTE
            registry.save(update_fields=["integration_note"])
