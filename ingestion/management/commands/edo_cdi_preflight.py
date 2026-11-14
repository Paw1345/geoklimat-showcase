import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.edo_cdi import preflight
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "EDO CDI metadata plan or bounded --fetch; no raster, database writes or approval."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--fetch", action="store_true")

    def handle(self, *args, **options):
        try:
            result = preflight(fetch=options["fetch"])
        except FetchError as exc:
            raise CommandError(
                "EDO CDI preflight unavailable: access or metadata validation"
            ) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
