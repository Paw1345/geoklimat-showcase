import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.bdl_manifest import fetch_manifest, plan
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Plan or fetch a bounded RDLP identity manifest, no geometry or DB writes."

    def add_arguments(self, parser):
        parser.add_argument("--fetch", action="store_true")

    def handle(self, *args, **options):
        try:
            result = fetch_manifest(Client()) if options["fetch"] else plan()
        except (FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError("BDL manifest failed: profile, policy, transport or schema") from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
