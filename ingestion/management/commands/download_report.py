import json

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder

from ingestion.download_report import report


class Command(BaseCommand):
    help = (
        "Read-only file download state: cursor, chunk continuity, resumability; "
        "no HTTP, retries, restarts or repairs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, action="append", help="DownloadJob id")
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Also hash every chunk payload and the whole file (reads all stored bytes).",
        )

    def handle(self, *args, **options):
        result = report(job_ids=options["job"], verify=options["verify"])
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False))
