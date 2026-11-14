import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from ingestion.models import DownloadJob
from ingestion.prtr_releases import read_verified_releases
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Read a verified local PL2017 PRTR copy; statistics only, no HTTP or writes."

    def add_arguments(self, parser):
        parser.add_argument("--job", required=True, type=int)

    def handle(self, *args, **options):
        if connection.vendor != "postgresql" or connection.in_atomic_block:
            raise CommandError("PRTR inspection requires a standalone PostgreSQL transaction")
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                job = DownloadJob.objects.select_related("channel").get(pk=options["job"])
                _, summary = read_verified_releases(job, timezone.now())
        except (DownloadJob.DoesNotExist, FetchError) as error:
            raise CommandError(
                "PRTR inspection refused: unavailable or invalid reviewed copy"
            ) from error
        self.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True))
