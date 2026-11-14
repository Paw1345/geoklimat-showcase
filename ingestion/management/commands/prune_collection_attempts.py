import json

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from ingestion.collection_evidence import METADATA_RETENTION_DAYS, RETENTION_BATCH, prune_attempts


class Command(BaseCommand):
    help = (
        f"Preview {METADATA_RETENTION_DAYS}-day collection-metadata retention; "
        "--apply deletes one bounded batch only. "
        "OFF performs no SQL."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=RETENTION_BATCH)

    def handle(self, *args, **options):
        result = prune_attempts(apply=options["apply"], limit=options["limit"])
        if result["status"] == "invalid":
            raise CommandError("Invalid collection-metadata retention options")
        self.stdout.write(json.dumps(result, cls=DjangoJSONEncoder))
