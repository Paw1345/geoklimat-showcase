import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from ingestion.stac_capacity import report


class Command(BaseCommand):
    help = "Read-only STAC storage scenarios; no HTTP, activation, pruning or repairs."

    def add_arguments(self, parser):
        parser.add_argument("--daily-budget-mib", type=int, default=256)
        parser.add_argument("--reserve-mib", type=int, default=1024)

    def handle(self, *args, **options):
        try:
            result = report(options["daily_budget_mib"], options["reserve_mib"])
        except ValueError as exc:
            raise CommandError("Invalid STAC capacity scenario or channel limit") from exc
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False))
