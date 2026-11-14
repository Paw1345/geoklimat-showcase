import logging
import signal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from ingestion.collection_evidence import RETENTION_BATCH, _worker_attempt_scope
from ingestion.data_service import run_data
from ingestion.downloads import run_downloads
from ingestion.heartbeat import ping
from ingestion.service import run_batch
from ingestion.worker_presence import WorkerPresence
from ingestion.worker_stop import STOP

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Poll PostgreSQL schedule every 30 seconds. SIGTERM stops cleanly after the current "
        "channel, page, download job or news source. A worker holds the worker advisory "
        "lock for its lifetime and does no work while another process holds it."
    )

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        stopped = STOP
        stopped.clear()
        if not options["once"]:
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
        presence = WorkerPresence()
        try:
            self.loop(stopped, presence, options)
        finally:
            presence.release()
        if stopped.is_set():
            logger.info("Worker stopped on signal")

    def loop(self, stopped, presence, options):
        waiting = False
        while not stopped.is_set():
            close_old_connections()
            if not presence.ensure():
                if options["once"]:
                    raise CommandError("Another worker or profile change holds the worker lock")
                if not waiting:
                    logger.warning("Worker lock held elsewhere; waiting without collecting")
                waiting = True
                stopped.wait(30)
                continue
            waiting = False
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
                if not stopped.is_set():
                    call_command("prune_data", apply=True, verbosity=0)
                if not stopped.is_set() and settings.COLLECTION_EVIDENCE_ENABLED is True:
                    call_command(
                        "prune_collection_attempts", apply=True, limit=RETENTION_BATCH, verbosity=0
                    )
                with _worker_attempt_scope():
                    for result in run_data():
                        logger.info("Data collection: %s", result)
                # Bound news work per cycle so slow feeds cannot postpone data indefinitely.
                for result in run_batch(limit=5):
                    logger.info("Ingestion result: %s", result)
                # Reached only after a cycle without an exception; a stalled worker stops pinging.
                ping()
            except Exception as exc:
                logger.error("Worker cycle failed: %s", type(exc).__name__)
                if options["once"]:
                    raise
            if options["once"]:
                break
            stopped.wait(30)
