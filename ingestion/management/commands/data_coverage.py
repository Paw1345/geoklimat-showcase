import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from ingestion.data_coverage import MODES, coverage_report
from ingestion.models import DataChannel


class Command(BaseCommand):
    help = "Read-only PIG scan coverage; optional local payload verification, no HTTP or writes."

    def add_arguments(self, parser):
        parser.add_argument("--source")
        parser.add_argument("--verify-payloads", action="store_true")
        parser.add_argument(
            "--require-verified",
            action="store_true",
            help="Require full verified coverage for every selected scan (checks payloads).",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            channels = DataChannel.objects.filter(config__mode__in=sorted(MODES)).order_by("pk")
            if options["source"]:
                channels = channels.filter(slug=options["source"])
                if not channels.exists():
                    raise CommandError("Select a reviewed PIG identity scan")
            results = []
            for channel in channels:
                h = channel.harvests.defer("page_fingerprints", "visited_urls").first()
                if h:
                    h.channel = channel
                    results.append(
                        coverage_report(
                            h,
                            verify_payloads=options["verify_payloads"]
                            or options["require_verified"],
                        )
                    )
                else:
                    results.append({"channel": channel.slug, "status": "not_started"})
            self.stdout.write(json.dumps(results, ensure_ascii=False))
            if options["require_verified"] and (
                not results or any(not result.get("scope_verified", False) for result in results)
            ):
                raise CommandError(
                    "Full verified coverage is unavailable for the selected PIG scans"
                )
