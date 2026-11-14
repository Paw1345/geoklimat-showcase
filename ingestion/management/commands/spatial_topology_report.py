"""Read-only topology defect report for one retained PIG spatial scan."""

import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.models import DataChannel
from ingestion.spatial_topology import report
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = (
        "List invalid geometries (ID, copy page, snapshot SHA-256, GEOS reason) in "
        "verified retained copies; no HTTP, no repair, no writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True, help="DataChannel slug")
        parser.add_argument("--harvest", type=int)

    def handle(self, *args, **options):
        channel = DataChannel.objects.filter(slug=options["source"]).first()
        if not channel:
            raise CommandError("Select an existing spatial channel")
        try:
            result = report(channel.pk, options["harvest"])
        except FetchError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
