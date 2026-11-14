import json

from django.core.management.base import BaseCommand

from ingestion.gdos_preflight import preflight


class Command(BaseCommand):
    help = "R016 GDOŚ SDI plan or bounded --fetch of robots/capabilities; no database writes."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--fetch", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(preflight(fetch=options["fetch"]), ensure_ascii=False))
