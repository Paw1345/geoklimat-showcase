import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from ingestion.jrc_figaro import AREAS, INDICATOR_UNITS, read_verified_figaro
from ingestion.models import DownloadJob
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Read the verified local FIGARO2015 copy; raw bounded sample, no HTTP or writes."

    def add_arguments(self, parser):
        parser.add_argument("--job", required=True, type=int)
        parser.add_argument("--area", choices=sorted(AREAS))
        parser.add_argument("--indicator", choices=sorted(INDICATOR_UNITS))
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **options):
        area, indicator, limit = options["area"], options["indicator"], options["limit"]
        if (
            type(limit) is not int
            or not 1 <= limit <= 50
            or (area is not None and area not in AREAS)
            or (indicator is not None and indicator not in INDICATOR_UNITS)
        ):
            raise CommandError("FIGARO selection invalid; limit must be between 1 and 50")
        if connection.vendor != "postgresql" or connection.in_atomic_block:
            raise CommandError("FIGARO inspection requires a standalone PostgreSQL transaction")
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                job = DownloadJob.objects.select_related("channel").get(pk=options["job"])
                rows, summary = read_verified_figaro(job, timezone.now())
                sample, matched = [], 0
                for row in rows:
                    if (area is None or row["refArea"] == area) and (
                        indicator is None or row["codeIndicator"] == indicator
                    ):
                        matched += 1
                        if len(sample) < limit:
                            sample.append(row)
                summary.update(
                    area_filter=area,
                    indicator_filter=indicator,
                    matching_records=matched,
                    sample_limit=limit,
                    sample=sample,
                )
        except (DownloadJob.DoesNotExist, FetchError) as error:
            raise CommandError(
                "FIGARO inspection refused: unavailable or invalid reviewed copy"
            ) from error
        self.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
