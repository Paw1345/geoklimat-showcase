import json

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder

from ingestion.history_report import report


class Command(BaseCommand):
    help = (
        "Read-only 30-day observation history and gaps per timed series, with quota "
        "totals; no fetching, pruning, cursor changes or repairs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--channel", type=int, action="append", help="DataChannel id")
        parser.add_argument(
            "--imgw-pilot-window",
            action="store_true",
            help="Assess the approved 2026-10-15 IMGW window separately; no collection or writes.",
        )

    def handle(self, *args, **options):
        result = report(
            channel_ids=options["channel"], imgw_pilot_window=options["imgw_pilot_window"]
        )
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False))
