import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from ingestion.epic45_scope_cards import RESULT_BYTES, markdown, scope_cards
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Read-only 219 scope cards and named review tasks; never accepts products or terms."

    def add_arguments(self, parser):
        parser.add_argument("--format", choices=["json", "markdown"], default="json")

    def handle(self, *args, **options):
        try:
            report = scope_cards()
            result = (
                markdown(report)
                if options["format"] == "markdown"
                else json.dumps(report, cls=DjangoJSONEncoder, ensure_ascii=False)
            )
            if len(result.encode()) > RESULT_BYTES:
                raise FetchError("Scope-card output budget exceeded")
        except FetchError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(result)
