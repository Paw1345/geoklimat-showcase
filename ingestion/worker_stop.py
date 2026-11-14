"""Cooperative stop request set by the ingestion worker's SIGTERM handler.

Loops check it between committed units (channel, harvest page, download job, news
source), so a stop ends work after the current unit, not after the whole cycle.
"""

import threading

STOP = threading.Event()


def requested():
    return STOP.is_set()
