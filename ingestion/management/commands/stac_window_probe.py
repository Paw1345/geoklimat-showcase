import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from ingestion.models import DataChannel
from ingestion.stac_recent import PROFILES
from ingestion.stac_window_probe import measure, plan
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Dry-plan or disposable bounded STAC GET probe; no snapshots/cursors/config writes."

    def add_arguments(self, parser):
        parser.add_argument("--region", choices=["PL", "EU"], required=True)
        parser.add_argument("--fetch", action="store_true")
        parser.add_argument("--pages", type=int, default=6)
        parser.add_argument("--budget-mib", type=int, default=8)
        parser.add_argument("--seconds", type=int, default=60)

    def handle(self, *args, **options):
        try:
            slug = next(key for key, value in PROFILES.items() if value[0] == options["region"])
            limits = {
                "pages": options["pages"],
                "budget_mib": options["budget_mib"],
                "seconds": options["seconds"],
            }
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                channel = DataChannel.objects.get(slug=slug + "-recent")
                result = plan(channel, **limits)
            if options["fetch"]:
                result = measure(channel, Client(), **limits)
        except (DataChannel.DoesNotExist, FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError(
                "STAC window probe unavailable: limits, approval or profile"
            ) from exc
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False))
        if result.get("failed"):
            raise CommandError("STAC window probe stopped: fetch or validation failed")
