import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from ingestion.models import DownloadJob
from ingestion.natura_sites import read_verified_sites
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Inspect the reviewed local Natura2021 CSV; default plan, no HTTP or writes."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int)

    def handle(self, *args, **options):
        job_id = options["job"]
        if job_id is None:
            self.stdout.write(
                json.dumps(
                    {
                        "status": "plan",
                        "next": "Use --job ID for one reviewed local copy",
                        "http": False,
                        "database_writes": False,
                    }
                )
            )
            return
        if type(job_id) is not int or job_id <= 0:
            raise CommandError("Natura job must be a positive integer")
        if connection.vendor != "postgresql" or connection.in_atomic_block:
            raise CommandError("Natura report requires a standalone PostgreSQL transaction")
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                job = DownloadJob.objects.select_related("channel").get(pk=job_id)
                summary = read_verified_sites(job, timezone.now())
        except (DownloadJob.DoesNotExist, FetchError) as error:
            raise CommandError(
                "Natura report refused: unavailable or invalid reviewed copy"
            ) from error
        self.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
