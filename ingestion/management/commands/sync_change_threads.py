from django.core.management.base import BaseCommand, CommandError

from ingestion.changes import ensure_thread
from ingestion.models import LegalProcess
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Create threads for existing legislative processes; dry-run default, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired:
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            missing = LegalProcess.objects.filter(change_thread__isnull=True)
            count = missing.count()
            if options["apply"]:
                for process in missing.iterator(chunk_size=500):
                    ensure_thread(process)
            self.stdout.write(f"Missing threads: {count}; applied={options['apply']}")
