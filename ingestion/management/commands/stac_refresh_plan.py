import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from ingestion.stac_refresh import report


class Command(BaseCommand):
    help = "Read-only STAC refresh plan; reuse channels, no HTTP or resets."

    def handle(self, *args, **options):
        try:
            result = report()
        except ValueError as exc:
            raise CommandError("STAC refresh plan unavailable: scope or channel limit") from exc
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False))
