import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from core.stac import report
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Read-only frozen-window STAC scan/copy metadata; no remote requests."

    def add_arguments(self, parser):
        parser.add_argument("--verify", action="store_true")
        parser.add_argument("--channel", type=int)
        parser.add_argument("--harvest", type=int)
        parser.add_argument("--start", type=int, default=1)

    def handle(self, *args, **options):
        params = {}
        if options["verify"]:
            if not options["channel"] or not options["harvest"]:
                raise CommandError("Verification requires --channel and --harvest")
            params = {
                "verify_channel": options["channel"],
                "verify_harvest": options["harvest"],
                "start": options["start"],
            }
        elif (
            options["channel"] is not None
            or options["harvest"] is not None
            or options["start"] != 1
        ):
            raise CommandError("Batch scope requires --verify")
        try:
            result = report(**params)
        except (ValueError, FetchError) as exc:
            raise CommandError(
                "STAC batch unavailable: integrity, scope, retention or budget"
            ) from exc
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder, ensure_ascii=False))
