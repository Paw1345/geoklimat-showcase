import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.data_service import run_data
from ingestion.models import DataChannel


class Command(BaseCommand):
    help = "Collect bounded measurements/spatial data. Dry-run by default."

    def add_arguments(self, parser):
        parser.add_argument("--source")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--on-demand", action="store_true")

    def handle(self, *args, **options):
        if options["on_demand"] and not options["source"]:
            raise CommandError("--on-demand requires --source")
        if options["source"] and not DataChannel.objects.filter(slug=options["source"]).exists():
            raise CommandError("Unknown data channel")
        results = run_data(
            options["source"],
            dry_run=not options["apply"],
            force=options["force"],
            on_demand=options["on_demand"],
        )
        self.stdout.write(json.dumps(results, ensure_ascii=False))
        if any(row["status"] == "failed" for row in results):
            raise CommandError("Some data channels failed")
