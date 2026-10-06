from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.eea_press import CONFIG, NOTE, SLUG, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Create separate disabled EEA HTML press channel; dry-run/no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.filter(registry_id=175).first()
            newsroom = RegistryEntry.objects.filter(registry_id=174).first()
            originals = list(Source.objects.filter(slug__in=["eea-news", "eea-reports"]))
            companion = Source.objects.filter(slug=SLUG).first()
            if (
                not registry
                or not newsroom
                or len(originals) != 2
                or registry.integration_state != "linked"
            ):
                raise CommandError("Missing existing EEA registry/feed configuration")
            expected = {s.pk for s in originals} | ({companion.pk} if companion else set())
            if set(registry.sources.values_list("pk", flat=True)) != expected:
                raise CommandError("Preserving administrator registry linkage")
            primary = next(s for s in originals if s.slug == "eea-news")
            if set(newsroom.sources.values_list("pk", flat=True)) != (
                {primary.pk} | ({companion.pk} if companion else set())
            ):
                raise CommandError("Preserving administrator newsroom linkage")
            for source in originals:
                path = (
                    "eeas-press-releases-rss" if source.slug == "eea-news" else "publications-rss"
                )
                if (source.adapter, source.url, source.config, source.enabled) != (
                    "feed",
                    f"https://www.eea.europa.eu/en/newsroom/rss-feeds/{path}/rss.xml",
                    {},
                    False,
                ) or set(source.registry_entries.values_list("registry_id", flat=True)) != (
                    {174, 175} if source.slug == "eea-news" else {175}
                ):
                    raise CommandError("Preserving administrator RSS configuration/linkage")
            if companion:
                if (companion.adapter, companion.url, companion.config) != (
                    "web",
                    URL,
                    CONFIG,
                ) or set(companion.registry_entries.values_list("registry_id", flat=True)) != {
                    174,
                    175,
                }:
                    raise CommandError("Preserving administrator press channel")
                self.stdout.write("EEA press: unchanged; audit/history/approval preserved")
                return
            self.stdout.write("EEA press: create separate disabled first-page HTML channel")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            companion = Source.objects.create(
                slug=SLUG,
                name="EEA — komunikaty (oficjalna lista HTML)",
                organization=primary.organization,
                url=URL,
                adapter="web",
                config=CONFIG,
                language="en",
                priority=primary.priority,
                owner=primary.owner,
                reuse_notes=primary.reuse_notes,
                scope=NOTE,
                interval_minutes=360,
                enabled=False,
                activation_approved=False,
            )
            companion.topics.set(primary.topics.all())
            registry.sources.add(companion)
            newsroom.sources.add(companion)
