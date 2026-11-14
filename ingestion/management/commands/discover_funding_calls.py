import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.funding_search import discover
from ingestion.funding_search_transport import PROFILES, query, request_spec
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = (
        "Dry-run fixed LIFE/Horizon public search POST; --probe reads the page budget, "
        "no database writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--probe", action="store_true")
        parser.add_argument("--profile", choices=sorted(PROFILES), default="life")

    def handle(self, *args, **options):
        profile = options["profile"]
        url, body, content_type = request_spec(1, profile)
        self.stdout.write(
            json.dumps(
                {
                    "url": url,
                    "method": "POST",
                    "profile": profile,
                    "query": query(profile),
                    "body_bytes": len(body),
                    "content_type": content_type,
                    "database_writes": False,
                    "http": options["probe"],
                }
            )
        )
        if options["probe"]:
            try:
                report = discover(Client(), profile=profile)
            except FetchError as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(json.dumps(report, ensure_ascii=False))
