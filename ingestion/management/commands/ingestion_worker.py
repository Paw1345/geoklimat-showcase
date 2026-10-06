import logging
import signal
import threading

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ingestion.data_service import run_data
from ingestion.downloads import run_downloads
from ingestion.service import run_batch

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Poll PostgreSQL schedule every 30 seconds. SIGTERM stops cleanly after the current batch."
    )

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stopped = threading.Event()
        if not options["once"]:
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
        while not stopped.is_set():
            close_old_connections()
            try:
                call_command("collect_alerts", apply=True, verbosity=0)
            except Exception as exc:
                logger.error("Alert cycle failed: %s", type(exc).__name__)
                if options["once"]:
                    raise
            try:
                for result in run_downloads():
                    logger.info("File download: %s", result)
                # User-authorized rolling 30-day cache; registry/news are never pruned.
                call_command("prune_data", apply=True, verbosity=0)
                for result in run_data():
                    logger.info("Data collection: %s", result)
                # Bound news work per cycle so slow feeds cannot postpone data indefinitely.
                for result in run_batch(limit=5):
                    logger.info("Ingestion result: %s", result)
            except Exception as exc:
                logger.error("Worker cycle failed: %s", type(exc).__name__)
                if options["once"]:
                    raise
            if options["once"]:
                break
            stopped.wait(30)
