import json

from django.core.management.base import BaseCommand

from ingestion.discovery_457 import PROBES, discover


class Command(BaseCommand):
    help = "Read-only 4.5.7 interface discovery (EDO 200, EFFIS 202, ESA 204); no DB writes."

    def add_arguments(self, parser):
        parser.add_argument("--registry", type=int, action="append", choices=sorted(PROBES))

    def handle(self, *args, **options):
        result = discover(set(options["registry"] or []))
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=1))
