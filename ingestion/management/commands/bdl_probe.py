import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.bdl_probe import COLLECTIONS, plan, probe
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Dry-plan or disposable one-feature BDL GET probe; no DB writes or activation."

    def add_arguments(self, parser):
        parser.add_argument("--collection", choices=COLLECTIONS, default="rdlp")
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--fetch", action="store_true")

    def handle(self, *args, **options):
        try:
            result = (
                probe(options["collection"], Client(), options["offset"])
                if options["fetch"]
                else plan(options["collection"], options["offset"])
            )
        except (FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError("BDL probe failed: profile, policy, transport or schema") from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
