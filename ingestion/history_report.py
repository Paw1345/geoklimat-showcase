"""Read-only 30-day observation history per timed series (K4 / 4.5.9).

Counts retained observation timestamps in verified local copies, per calendar
day of the series' zone. A day without observations is a local gap with an
unknown cause unless evidenced, and retention is not a backfill. The only
provider-side cause recognised is a monthly IMGW daily archive that the newest
verified copy recorded as not yet published. News ingestion runs are a legacy
proxy, not per-channel evidence of data collection. For IMGW API series, neither
their presence nor absence establishes a collection cycle or a gap's cause.
Other timed kinds retain their legacy gap classification for compatibility;
none of these daily counts proves collection continuity. Static datasets (GeoJSON/WFS, files,
tables) are listed by copy dates only; completeness there is a scope check.
"""

import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.data_storage import report as storage_report
from ingestion.collection_evidence import METADATA_RETENTION_DAYS, read_attempts
from ingestion.models import DataChannel, DataSnapshot, IngestionRun
from ingestion.observations import (
    TIMED_KINDS,
    daily_window,
    filter_observations,
    retention_window,
)
from ingestion.transport import FetchError

BATCH = 10
MAX_CHANNELS = 1000
DAY_ZONE = {"stac": "UTC"}
DEFAULT_ZONE = "Europe/Warsaw"


def report(now=None, channel_ids=None, *, imgw_pilot_window=False):
    if type(imgw_pilot_window) is not bool:
        raise ValueError("Explicit boolean pilot-window choice required")
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError("Aware report time required")
    # Byte totals come from their own read-only snapshot of the cache report.
    storage = storage_report(now)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        channels = DataChannel.objects.order_by("pk")
        if channel_ids:
            channels = channels.filter(pk__in=channel_ids)
        channels = list(
            channels.values(
                "id",
                "slug",
                "name",
                "kind",
                "region",
                "enabled",
                "config",
                "url",
                "interval_minutes",
            )[: MAX_CHANNELS + 1]
        )
        if len(channels) > MAX_CHANNELS:
            raise ValueError("History report channel limit exceeded")
        evidence = read_attempts(channels, now=now, imgw_pilot_window=imgw_pilot_window)
        worker = {zone: _worker_days(now, zone) for zone in {DEFAULT_ZONE, *DAY_ZONE.values()}}
        rows = [
            _series(channel, now, worker, evidence)
            if channel["kind"] in TIMED_KINDS
            else _static(channel, now)
            for channel in channels
        ]
    series = [row for row in rows if row["time_series"]]
    result = {
        "as_of": now,
        "retention_days": retention_window().days,
        "worker_active_days": {zone: len(days) for zone, days in sorted(worker.items())},
        "worker_active_days_basis": "news_ingestion_run_proxy",
        "worker_continuity_confirmed": False,
        "collection_evidence_status": evidence["status"],
        "series": series,
        "static": [row for row in rows if not row["time_series"]],
        "series_with_gaps": sum(bool(row["days_without_observations"]) for row in series),
        "series_with_unverified_copies": sum(bool(row["unverified_copies"]) for row in series),
        "quota": {
            "quota_bytes": storage["quota_bytes"],
            "declared_bytes": storage["total"]["declared_bytes"],
            "actual_bytes": storage["total"]["actual_bytes"],
            "expired_bytes": storage["total"]["expired_bytes"],
            "remaining_logical_bytes": storage["remaining_logical_bytes"],
            "over_quota_bytes": storage["over_quota_bytes"],
            "within_quota": storage["over_quota_bytes"] == 0,
        },
        "agreed_series_confirmed": False,
        "source_gaps_documented": False,
        "disk_free_space_measured": False,
    }
    if imgw_pilot_window:
        from ingestion.collection_continuity import pilot_window_policy

        result["collection_acceptance"] = {
            "policy": pilot_window_policy(),
            "metadata_retention_days": METADATA_RETENTION_DAYS,
            "evidence_status": evidence["status"],
            "by_channel": evidence.get("acceptance_by_channel", {}),
            "scope": "reviewed IMGW PL API profiles only; not observation coverage or fleet PASS",
        }
    return result


def _live(channel_id, now):
    return DataSnapshot.objects.filter(
        channel_id=channel_id,
        download_job__isnull=True,
        fetched_at__gt=now - retention_window(),
        expires_at__gt=now,
    )


def _static(channel, now):
    live = _live(channel["id"], now).order_by("fetched_at").values_list("fetched_at", flat=True)
    dates = list(live)
    return {
        **_head(channel),
        "time_series": False,
        "live_copies": len(dates),
        "oldest_copy": dates[0] if dates else None,
        "newest_copy": dates[-1] if dates else None,
        "history_applicable": False,
    }


def _worker_days(now, zone):
    # Historical name only: any news run may be manual, unfinished or unrelated.
    runs = IngestionRun.objects.filter(started_at__gt=now - retention_window(), started_at__lte=now)
    days = runs.annotate(day=TruncDate("started_at", tzinfo=ZoneInfo(zone))).values_list(
        "day", flat=True
    )
    return {day.isoformat() for day in days.distinct()}


def _series(channel, now, worker, evidence):
    zone = DAY_ZONE.get(channel["kind"], DEFAULT_ZONE)
    days = _window(now, zone)
    counts = dict.fromkeys(days, 0)
    scanned, unverified, refused, oldest, newest = 0, [], [], None, None
    latest = None
    ids = list(
        _live(channel["id"], now)
        .filter(media_type="application/json")
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    for start in range(0, len(ids), BATCH):
        batch = DataSnapshot.objects.filter(pk__in=ids[start : start + BATCH]).order_by("pk")
        for snapshot in batch:
            payload = bytes(snapshot.payload)
            if (
                len(payload) != snapshot.size_bytes
                or hashlib.sha256(payload).hexdigest() != snapshot.sha256
            ):
                unverified.append(snapshot.pk)
                continue
            source_zone = snapshot.summary.get(
                "observation_timezone", channel["config"].get("timezone", "UTC")
            )
            kind = snapshot.summary.get("kind", channel["kind"])
            try:
                _, stamps = filter_observations(json.loads(payload), kind, now, source_zone)
            except (FetchError, ValueError):
                refused.append(snapshot.pk)
                continue
            scanned += 1
            if latest is None or snapshot.fetched_at >= latest.fetched_at:
                latest = snapshot
            for stamp in stamps:
                day = stamp.astimezone(ZoneInfo(zone)).date().isoformat()
                if day in counts:
                    counts[day] += 1
                oldest = stamp if oldest is None or stamp < oldest else oldest
                newest = stamp if newest is None or stamp > newest else newest
    expected = _expected(channel["kind"], days, now)
    missing = [day for day in expected if counts[day] == 0]
    # Evidence of a provider-side gap: the newest verified copy recorded that the monthly
    # archive was absent from the provider's listing when it was fetched (IMGW daily).
    unpublished = set(latest.summary.get("unpublished_months", [])) if latest else set()
    source_unpublished = [day for day in missing if day[:7] in unpublished]
    # Neither news proxies nor attempt absence explain observation gaps. Evidence
    # is reported separately; it does not change this legacy classification.
    not_collected = (
        []
        if channel["kind"] == "imgw"
        else [day for day in missing if day not in source_unpublished and day not in worker[zone]]
    )
    unexplained = [
        day for day in missing if day not in source_unpublished and day not in not_collected
    ]
    verified = not unverified and not refused
    return {
        **_head(channel),
        "time_series": True,
        "day_zone": zone,
        "window_start": now - retention_window(),
        "window_end": now,
        "copies_scanned": scanned,
        "unverified_copies": unverified,
        "refused_copies": refused,
        "oldest_observation": oldest.astimezone(UTC) if oldest else None,
        "newest_observation": newest.astimezone(UTC) if newest else None,
        "observation_occurrences_by_day": counts,
        "days_not_expected": [day for day in days if day not in expected],
        "days_without_observations": missing,
        "days_source_unpublished": source_unpublished,
        "days_no_collection_cycle": not_collected,
        "days_gap_unexplained": unexplained,
        "partial_days": [days[0], days[-1]] if days else [],
        "full_window_observed": verified and not missing and bool(scanned),
        "collection_continuity_confirmed": False,
        "collection_evidence": evidence["by_channel"].get(
            channel["id"],
            {
                "status": "disabled"
                if evidence["status"] == "disabled" and channel["kind"] == "imgw"
                else "not_applicable",
                "continuity_confirmed": False,
            },
        ),
        "gap_cause_known": verified and bool(scanned) and not unexplained,
    }


def _expected(kind, days, now):
    if kind != "imgw_daily":
        return days
    # The daily archive never contains today or the partial first day of the window.
    window = {day.isoformat() for day in daily_window(now)}
    return [day for day in days if day in window]


def _window(now, zone):
    local = ZoneInfo(zone)
    start = (now - retention_window()).astimezone(local)
    day, last, days = start.date(), now.astimezone(local).date(), []
    while day <= last:
        if datetime.combine(day + timedelta(days=1), time(), tzinfo=local) > start:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _head(channel):
    return {key: channel[key] for key in ("id", "slug", "name", "kind", "region", "enabled")}
