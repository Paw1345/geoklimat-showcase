import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.clms_lc100 import preflight
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "LC100 2019 metadata plan or bounded --fetch; no raster, SQL or approval."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--fetch", action="store_true")

    def handle(self, *args, **options):
        try:
            result = preflight(fetch=options["fetch"])
        except FetchError as exc:
            raise CommandError(
                "LC100 preflight unavailable: access or metadata validation"
            ) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
