from django.core.management.base import BaseCommand, CommandError

from ingestion.downloads import enqueue
from ingestion.models import DataChannel
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Queue one approved file without fetching it; dry-run default."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        channel = DataChannel.objects.filter(
            slug=options["source"], kind="file", access_approved=True, region__in=["PL", "EU"]
        ).first()
        if not channel:
            raise CommandError("Select an approved PL/EU file channel")
        if not options["apply"]:
            self.stdout.write(f"Would queue {channel.slug}; no writes or HTTP")
            return
        try:
            job, created = enqueue(channel)
        except FetchError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"job={job.pk}; created={created}; status={job.status}")
