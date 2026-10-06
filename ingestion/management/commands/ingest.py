import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.models import Source
from ingestion.service import run_batch


class Command(BaseCommand):
    help = "Fetch due approved sources. --dry-run performs no database writes."

    def add_arguments(self, parser):
        parser.add_argument("--source")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        slug = options["source"]
        if slug and not Source.objects.filter(slug=slug).exists():
            raise CommandError("Unknown source")
        if slug and not options["dry_run"] and not Source.objects.get(slug=slug).enabled:
            raise CommandError("Source is disabled; audit and approve it before ingestion")
        results = run_batch(slug, options["force"], options["dry_run"])
        self.stdout.write(json.dumps(results, ensure_ascii=False))
        if any(row["status"] == "busy" for row in results):
            raise CommandError("Another ingestion/audit process holds the lock; retry later")
        if any(row["status"] == "failed" for row in results):
            raise CommandError("Some sources failed; other sources were processed")
