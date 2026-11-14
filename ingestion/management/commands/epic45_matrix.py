import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from ingestion.epic45_matrix import inventory, markdown
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Read-only 219-entry acceptance inventory; no HTTP, writes or automatic acceptance."

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=["json", "markdown"], default="json")

    def handle(self, *args, **options):
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                report = inventory()
                result = (
                    markdown(report)
                    if options["format"] == "markdown"
                    else json.dumps(report, cls=DjangoJSONEncoder, ensure_ascii=False)
                )
        except FetchError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(result)
