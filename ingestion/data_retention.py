"""Expire individual observations without discarding their newer page neighbours."""

import hashlib
import json

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from ingestion.models import DataSnapshot, DownloadJob
from ingestion.observations import (
    TIMED_KINDS,
    filter_observations,
    observation_summary,
    retention_window,
)
from ingestion.transport import FetchError


def retained_snapshot(snapshot, now):
    """No writes. Also used at the HTTP boundary when maintenance is delayed."""
    summary = snapshot.summary
    kind = summary.get("kind", snapshot.channel.kind)
    hard_expiry = snapshot.fetched_at + retention_window()
    if snapshot.download_job_id or hard_expiry <= now:
        return None
    managed = (
        kind in TIMED_KINDS
        and snapshot.media_type == "application/json"
        and (summary.get("oldest_observation") or kind == "imgw_daily")
    )
    if not managed:
        if snapshot.expires_at <= now:
            return None
        return bytes(snapshot.payload), summary, min(hard_expiry, snapshot.expires_at)
    zone = summary.get("observation_timezone", snapshot.channel.config.get("timezone", "UTC"))
    data, stamps = filter_observations(json.loads(bytes(snapshot.payload)), kind, now, zone)
    if not stamps and kind != "imgw_daily":
        return None
    summary = observation_summary(summary, stamps, zone)
    rows = (
        data
        if kind == "imgw"
        else data[
            "features"
            if kind == "stac"
            else "records"
            if kind == "imgw_daily"
            else "Lista danych pomiarowych"
        ]
    )
    summary["records"] = len(rows)
    if kind == "imgw_daily":
        summary["coverage"] = data["coverage"]
    return (
        json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
        summary,
        min(hard_expiry, max(stamps) + retention_window())
        if stamps
        else min(hard_expiry, snapshot.expires_at),
    )


def prune_cache(apply=False):
    """Bounded memory, quota-locked per snapshot. Dry run changes nothing."""
    now = timezone.now()
    candidates = DataSnapshot.objects.filter(
        Q(expires_at__lte=now)
        | Q(summary__oldest_observation__lte=(now - retention_window()).isoformat())
        | (Q(summary__has_key="oldest_observation") & ~Q(summary__has_key="retention_version"))
    ).values_list("pk", flat=True)
    stats = {"deleted": 0, "trimmed": 0, "upgraded": 0, "bytes_freed": 0, "invalid": 0}
    for pk in candidates.iterator(chunk_size=100):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(726043)")
            snapshot = DataSnapshot.objects.select_related("channel").filter(pk=pk).first()
            if not snapshot:
                continue
            try:
                retained = retained_snapshot(snapshot, now)
            except (ValueError, TypeError, FetchError):
                # Fail closed at the export boundary; never extend an unreadable cache entry.
                stats["invalid"] += 1
                if snapshot.expires_at > now:
                    continue
                retained = None
            if retained is None:
                stats["deleted"] += 1
                stats["bytes_freed"] += snapshot.size_bytes
                if apply:
                    snapshot.delete()
                continue
            payload, summary, expires = retained
            if payload != bytes(snapshot.payload):
                stats["trimmed"] += 1
            elif summary == snapshot.summary and expires == snapshot.expires_at:
                continue
            else:
                stats["upgraded"] += 1
            delta = len(payload) - snapshot.size_bytes
            if delta > 0:
                used = DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
                if used + delta > settings.DATA_STORAGE_MAX_BYTES:
                    raise FetchError("Observation maintenance would exceed data quota")
            stats["bytes_freed"] -= delta
            if apply:
                snapshot.payload = payload
                snapshot.size_bytes = len(payload)
                snapshot.sha256 = hashlib.sha256(payload).hexdigest()
                snapshot.summary = summary
                snapshot.expires_at = expires
                snapshot.save(
                    update_fields=["payload", "size_bytes", "sha256", "summary", "expires_at"]
                )
    if apply:
        DownloadJob.objects.filter(expires_at__lte=now).update(status="expired")
    return stats
