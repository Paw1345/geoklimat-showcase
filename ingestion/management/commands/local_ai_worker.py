import signal
import threading

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ingestion.local_ai import run_one


class Command(BaseCommand):
    help = "Consume local AI drafts, single inference at a time. No external APIs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stopped = threading.Event()
        if not options["once"]:
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        while not stopped.is_set():
            close_old_connections()
            self.stdout.write(f"Local AI: {run_one()}")
            if options["once"]:
                break
            stopped.wait(5)
