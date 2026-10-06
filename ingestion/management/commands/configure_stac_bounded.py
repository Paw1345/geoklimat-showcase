import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.management.commands.configure_stac_recent import configuration_lock
from ingestion.models import DataChannel
from ingestion.stac_bounded import defaults
from ingestion.stac_recent import PROFILES
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Dry-plan/create DISABLED bounded STAC scans; no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--wait-seconds", type=int, default=0)

    def handle(self, *args, **options):
        if not 0 <= options["wait_seconds"] <= 30:
            raise CommandError("Wait budget must be between 0 and 30 seconds")
        try:
            with configuration_lock(options["wait_seconds"]), transaction.atomic():
                rows = []
                for slug in PROFILES:
                    parent = DataChannel.objects.select_for_update().get(slug=slug)
                    expected = defaults(parent)
                    child = (
                        DataChannel.objects.select_for_update()
                        .filter(slug=slug + "-bounded")
                        .first()
                    )
                    if child and any(
                        getattr(child, key) != value for key, value in expected.items()
                    ):
                        raise FetchError("Modified bounded profile; preserve administrator edits")
                    if options["apply"] and child is None:
                        child = DataChannel.objects.create(slug=slug + "-bounded", **expected)
                    rows.append(
                        {
                            "slug": slug + "-bounded",
                            "apply": options["apply"],
                            "activation_requested": False,
                            "history_modified": False,
                        }
                    )
            self.stdout.write(json.dumps(rows))
        except (DataChannel.DoesNotExist, FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError(
                "Bounded STAC configuration unavailable; no profiles changed"
            ) from exc
