import json

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder

from core.data_storage import report


class Command(BaseCommand):
    help = "Read-only cache bytes/expiry report; no fetching, pruning or repairs."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(report(), cls=DjangoJSONEncoder, ensure_ascii=False))
