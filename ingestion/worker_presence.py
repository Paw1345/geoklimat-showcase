"""Checkable proof that no ingestion worker is running against this database.

A worker holds a session-level PostgreSQL advisory lock on its own dedicated
connection for its whole lifetime, including the sleep between cycles. PostgreSQL
releases the lock when the process or its connection ends, so a caller that can
take the same lock has proof that no worker of this version is running. The
proof does not cover workers started from code older than this module.
"""

import logging
from contextlib import contextmanager

from django.db import DEFAULT_DB_ALIAS, connections

logger = logging.getLogger(__name__)

WORKER_LOCK = 726044
HELD_SQL = (
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
    "AND pid = pg_backend_pid() AND classid = 0 AND objid = %s AND objsubid = 1)"
)


class WorkerPresence:
    """Advisory lock held by a running worker on a connection Django never recycles."""

    def __init__(self, alias=DEFAULT_DB_ALIAS):
        self.alias = alias
        self.connection = None

    def _query(self, sql):
        with self.connection.cursor() as cursor:
            cursor.execute(sql, [WORKER_LOCK])
            return bool(cursor.fetchone()[0])

    def ensure(self):
        """Return True only while this process verifiably holds the worker lock."""
        if self.connection is not None:
            try:
                if self._query(HELD_SQL):
                    return True
            except Exception as exc:
                logger.warning("Worker lock connection lost: %s", type(exc).__name__)
            self.release()
        try:
            self.connection = connections[self.alias].copy()
            if self._query("SELECT pg_try_advisory_lock(%s)"):
                return True
        except Exception as exc:
            logger.warning("Worker lock unavailable: %s", type(exc).__name__)
        self.release()
        return False

    def release(self):
        if self.connection is not None:
            try:
                # Closing the session releases the advisory lock in PostgreSQL.
                self.connection.close()
            except Exception as exc:
                logger.warning("Worker lock close failed: %s", type(exc).__name__)
            self.connection = None


@contextmanager
def workers_stopped(using=DEFAULT_DB_ALIAS):
    """Yield True when no worker holds the lock; hold it so none can start meanwhile."""
    connection = connections[using]
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [WORKER_LOCK])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [WORKER_LOCK])
