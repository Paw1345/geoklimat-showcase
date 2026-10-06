import json

from django.core.management.base import BaseCommand

from core.quality import report


class Command(BaseCommand):
    help = "Read-only aggregate quality and source coverage report. No HTTP, no repairs."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(report(), ensure_ascii=False, indent=2))
