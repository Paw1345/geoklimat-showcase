from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.models import LegalEvent
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Withdraw an erroneous editorial date without deleting its evidence; dry-run default."

    def add_arguments(self, parser):
        parser.add_argument("event", type=int)
        parser.add_argument("--reviewer", required=True)
        parser.add_argument("--note", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if (
            not 1 <= len(options["reviewer"].strip()) <= 150
            or not 10 <= len(options["note"].strip()) <= 2000
        ):
            raise CommandError("Reviewer and a specific withdrawal reason are required")
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            event = LegalEvent.objects.filter(pk=options["event"], link__origin="editorial").first()
            if event is None:
                raise CommandError(
                    "Editorial event not found; automatic evidence must be refreshed"
                )
            if event.superseded_at:
                self.stdout.write("Already superseded; history unchanged")
                return
            self.stdout.write(f"Withdraw event {event.pk}; applied={options['apply']}")
            if options["apply"]:
                event.superseded_at = timezone.now()
                event.withdrawal_note = f"{options['reviewer']}: {options['note']}"
                event.save(update_fields=["superseded_at", "withdrawal_note"])
