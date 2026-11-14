import json

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from ingestion.epic45_queue import operational_queue


class Command(BaseCommand):
    help = "Read-only operational triage, no HTTP/repairs/activation or raw configuration."

    def handle(self, *args, **options):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            report = operational_queue()
        self.stdout.write(json.dumps(report, cls=DjangoJSONEncoder, ensure_ascii=False))
