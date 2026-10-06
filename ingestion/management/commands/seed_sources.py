from django.core.management.base import BaseCommand
from django.db import transaction

from ingestion.catalog import CATALOG, TOPICS
from ingestion.models import Source, Topic


class Command(BaseCommand):
    help = "Seed editorial catalog, preserving existing administrator changes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--refresh-catalog",
            action="store_true",
            help="Explicitly replace catalog metadata; disable changed sources for re-audit",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        for slug, (name, keywords) in TOPICS.items():
            Topic.objects.get_or_create(slug=slug, defaults={"name": name, "keywords": keywords})
        created_count = 0
        for row in CATALOG:
            values = dict(row)
            topics = values.pop("topics")
            hold = values.pop("manual_hold", False)
            slug = values.pop("slug")
            values["reuse_notes"] = (
                "Metadane, tytuł i krótki fragment z linkiem; nie kopiujemy całych publikacji. "
                "Licencja pełnej treści: niezweryfikowana; robots sprawdzane przy pobraniu."
            )
            if hold:
                values["reuse_notes"] += " Wstrzymane redakcyjnie: wymaga adaptera projektów."
            source, created = Source.objects.get_or_create(slug=slug, defaults=values)
            if not created and options["refresh_catalog"]:
                changed = any(getattr(source, field) != value for field, value in values.items())
                if changed:
                    for field, value in values.items():
                        setattr(source, field, value)
                    source.enabled = False
                    source.etag = source.last_modified = ""
                    source.audited_at = None
                    source.last_audit = {}
                    source.save()
                    source.topics.set(Topic.objects.filter(slug__in=topics))
            if created:
                source.topics.set(Topic.objects.filter(slug__in=topics))
                created_count += 1
        self.stdout.write(f"Created {created_count}; total sources: {Source.objects.count()}")
