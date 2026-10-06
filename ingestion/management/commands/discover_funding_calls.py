import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.funding_search import discover
from ingestion.funding_search_transport import QUERY, request_spec
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = (
        "Dry-run fixed LIFE public search POST; --probe reads up to five pages, no database writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true")

    def handle(self, *args, **options):
        url, body, content_type = request_spec(1)
        self.stdout.write(
            json.dumps(
                {
                    "url": url,
                    "method": "POST",
                    "query": QUERY,
                    "body_bytes": len(body),
                    "content_type": content_type,
                    "database_writes": False,
                    "http": options["probe"],
                }
            )
        )
        if options["probe"]:
            try:
                report = discover(Client())
            except FetchError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(json.dumps(report, ensure_ascii=False))
