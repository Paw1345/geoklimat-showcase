from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.opn_documents import CONFIG, NOTE, SLUG, URL
from ingestion.opn_news import CONFIG as NEWS_CONFIG
from ingestion.opn_news import URL as NEWS_URL
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare separate OPN protection catalogue; dry-run/no HTTP; preserve news."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.filter(registry_id=151).first()
            primary = Source.objects.filter(slug="registry-151").first()
            companion = Source.objects.filter(slug=SLUG).first()
            if (
                not registry
                or registry.integration_state != "configured"
                or not primary
                or (primary.adapter, primary.url, primary.config) != ("web", NEWS_URL, NEWS_CONFIG)
                or not registry.sources.filter(pk=primary.pk).exists()
                or registry.sources.exclude(slug__in=["registry-151", SLUG]).exists()
                or list(primary.registry_entries.values_list("registry_id", flat=True)) != [151]
            ):
                raise CommandError("Preserving administrator registry/news configuration")
            if companion:
                if (
                    (companion.adapter, companion.url, companion.config) != ("web", URL, CONFIG)
                    or not registry.sources.filter(pk=companion.pk).exists()
                    or list(companion.registry_entries.values_list("registry_id", flat=True))
                    != [151]
                ):
                    raise CommandError("Preserving administrator catalogue/linkage")
                self.stdout.write(SLUG + ": unchanged; history/audit/approval preserved")
                return
            self.stdout.write(SLUG + ": create disabled separate protection catalogue")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            companion = Source.objects.create(
                slug=SLUG,
                name="OPN — katalog dokumentów ochronnych",
                organization=primary.organization,
                adapter="web",
                url=URL,
                config=CONFIG,
                language=primary.language,
                priority=primary.priority,
                owner=primary.owner,
                interval_minutes=1440,
                scope=NOTE,
                enabled=False,
                activation_approved=False,
            )
            companion.topics.set(primary.topics.all())
            registry.sources.add(companion)
