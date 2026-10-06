import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.ems_catalogue import CONFIG, MAX_BYTES, URL, parse_page
from ingestion.models import Source
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Dry-plan or disposable first-page EMS GET; no database writes or activation."

    def add_arguments(self, parser):
        parser.add_argument("--fetch", action="store_true")

    def handle(self, *args, **options):
        result = {
            "request_url": URL,
            "max_bytes": MAX_BYTES,
            "max_pages": 1,
            "read_only": True,
            "fetched": False,
        }
        if options["fetch"]:
            try:
                client = Client()
                client.max_bytes = MAX_BYTES
                response = client.get(URL)
                source = Source(adapter="json", url=URL, config=CONFIG)
                entries, total, read = parse_page(source, response)
                result.update(
                    fetched=True,
                    status=response.status,
                    access_policy=response.access_policy,
                    bytes=len(response.body),
                    provider_count=total,
                    read_rows=read,
                    matched_eu_rows=len(entries),
                    activation_codes=[entry.key for entry in entries],
                    full_archive_coverage=False,
                    provider_next_followed=False,
                )
            except (FetchError, ValueError) as exc:
                raise CommandError("EMS probe unavailable: access, schema or scope") from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
