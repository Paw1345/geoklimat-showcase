from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.eu_authority_tables import CONFIG, NOTE, SLUG, URL
from ingestion.inspire_codes import PROFILES
from ingestion.inspire_vocabularies import CONFIG as PRIMARY_CONFIG
from ingestion.inspire_vocabularies import SLUG as PRIMARY_SLUG
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare disabled EU authority tables catalogue (registry 180); dry-run, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.filter(registry_id=180).first()
            primary = Source.objects.filter(slug=PRIMARY_SLUG).first()
            if (
                not registry
                or registry.integration_state != "needs_connector"
                or not primary
                or (primary.adapter, primary.url, primary.config) != ("json", URL, PRIMARY_CONFIG)
                or not registry.sources.filter(pk=primary.pk).exists()
                or registry.sources.exclude(slug__in=[PRIMARY_SLUG, SLUG, *PROFILES]).exists()
            ):
                raise CommandError("Preserving administrator registry/catalogue configuration")
            source = Source.objects.filter(slug=SLUG).first()
            if source:
                if (source.adapter, source.url, source.config) != (
                    "json",
                    URL,
                    CONFIG,
                ) or list(source.registry_entries.values_list("registry_id", flat=True)) != [180]:
                    raise CommandError("Preserving administrator configuration/linkage")
                self.stdout.write(SLUG + ": unchanged; audit/history/approval preserved")
                return
            self.stdout.write(SLUG + ": create separate disabled catalogue; full 180 stays open")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            source = Source.objects.create(
                slug=SLUG,
                name="EU Vocabularies — katalog tabel autorytatywnych",
                organization=primary.organization,
                adapter="json",
                url=URL,
                config=CONFIG.copy(),
                language="en",
                priority=primary.priority,
                owner=primary.owner,
                scope=NOTE,
                reuse_notes=primary.reuse_notes,
                interval_minutes=1440,
                enabled=False,
                activation_approved=False,
            )
            source.topics.set(primary.topics.all())
            registry.sources.add(source)
