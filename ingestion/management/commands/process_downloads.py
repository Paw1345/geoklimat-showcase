import json

from django.core.management.base import BaseCommand

from ingestion.downloads import run_downloads


class Command(BaseCommand):
    help = "Process at most one chunk for each of five explicitly queued downloads."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(run_downloads(), ensure_ascii=False))
