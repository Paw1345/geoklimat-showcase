"""Optional outbound ping after a healthy worker cycle (healthchecks.io, Uptime Kuma).

The address comes only from the operator's environment. No ping is sent without it.
A failed ping never stops collection.
"""

import logging
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 300
TIMEOUT_SECONDS = 5
_last_sent = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(url):
    request = Request(url, headers={"User-Agent": settings.INGEST_USER_AGENT})
    with build_opener(_NoRedirect).open(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status


def ping(now=None):
    """Send at most one GET per MIN_INTERVAL_SECONDS. Returns True when a ping was sent."""
    global _last_sent
    url = getattr(settings, "WORKER_HEARTBEAT_URL", "")
    if not url:
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        logger.error("Heartbeat address rejected: it must be https without credentials")
        return False
    now = time.monotonic() if now is None else now
    if _last_sent is not None and now - _last_sent < MIN_INTERVAL_SECONDS:
        return False
    _last_sent = now
    try:
        _open(url)
    except Exception as exc:
        # The address can hold a secret token, so only the error type is logged.
        logger.warning("Heartbeat ping failed: %s", type(exc).__name__)
        return False
    return True
