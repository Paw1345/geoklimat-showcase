"""Read-only refresh routing; reuse reviewed channels, never restart old scans."""

import math
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from ingestion.models import DataChannel
from ingestion.stac_bounded import RESERVE_BYTES, WINDOW_BYTES
from ingestion.stac_bounded import validate_profile as validate_bounded
from ingestion.stac_capacity import capacity
from ingestion.stac_coverage import search_scope
from ingestion.stac_recent import PROFILES
from ingestion.stac_recent import validate_profile as validate_recent
from ingestion.transport import FetchError


def channel_state(channel, now, validate=None):
    if channel is None:
        return {"channel_id": None, "state": "missing", "active": False}
    harvest = channel.harvests.defer("visited_urls", "page_fingerprints").first()
    result = {
        "channel_id": channel.pk,
        "active": channel.enabled and channel.access_approved,
        "state": "not_started",
        "profile_valid": False,
        "harvest_id": harvest.pk if harvest else None,
        "next_fetch_at": channel.next_fetch_at,
        "due": channel.next_fetch_at is None or channel.next_fetch_at <= now,
        "search_start": None,
        "search_end": None,
        "search_end_lag_seconds": None,
        "scan_complete": False,
    }
    try:
        if validate:
            validate(channel)
        result["profile_valid"] = True
        if harvest:
            scope = search_scope(channel, harvest)
            if scope["search_end"] > now:
                raise FetchError("Future STAC search window")
            result.update(
                state=harvest.status,
                search_start=scope["search_start"],
                search_end=scope["search_end"],
                search_end_lag_seconds=int((now - scope["search_end"]).total_seconds()),
                scan_complete=harvest.status == "complete",
            )
    except (FetchError, TypeError, AttributeError, ValueError):
        result.update(state="profile_or_scope_unavailable", profile_valid=False)
    return result


def plan(now):
    """Caller holds a consistent read-only transaction; no payloads or HTTP."""
    if timezone.is_naive(now):
        raise ValueError("Aware STAC plan time required")
    channels = list(DataChannel.objects.filter(kind="stac").order_by("pk")[:21])
    if len(channels) > 20:
        raise ValueError("STAC refresh channel limit exceeded")
    indexed = {channel.slug: channel for channel in channels}
    budget = capacity(now, daily_budget_mib=1, reserve_mib=RESERVE_BYTES // 1024**2)
    rows, known_bounded_ids = [], set()
    bounded_cost, bounded_known = 0, True
    for slug, (region, _) in PROFILES.items():
        history = channel_state(indexed.get(slug), now)
        recent = channel_state(indexed.get(slug + "-recent"), now, validate_recent)
        bounded = channel_state(indexed.get(slug + "-bounded"), now, validate_bounded)
        if bounded["channel_id"]:
            known_bounded_ids.add(bounded["channel_id"])
        if bounded["active"]:
            if not bounded.get("profile_valid"):
                bounded_known = False
            else:
                interval = indexed[slug + "-bounded"].interval_minutes
                bounded_cost += WINDOW_BYTES * math.ceil(1440 / interval) * budget["retention_days"]
        reusable = bounded["active"] and bounded.get("profile_valid", False)
        rows.append(
            {
                "region": region,
                "history": history,
                "recent": recent,
                "bounded": bounded,
                "recommended_action": "reuse_scheduled_bounded"
                if reusable
                else "review_existing_profile",
                "new_channel_needed": False if reusable else None,
                "candidate_search_start": now - timedelta(hours=24),
                "candidate_search_end": now,
                "candidate_window_pinned": False,
                "history_reset_allowed": False,
                "full_current_30_day_coverage_verified": False,
                "missing_scene_count": None,
            }
        )
    # An unrecognized active bounded profile must not silently cost zero.
    if any(
        channel.enabled
        and channel.access_approved
        and isinstance(channel.config, dict)
        and channel.config.get("mode") == "stac_bounded"
        and channel.pk not in known_bounded_ids
        for channel in channels
    ):
        bounded_known = False
    samples = budget["future_samples_retention_estimate_bytes"]
    projected = samples + bounded_cost if samples is not None and bounded_known else None
    return {
        "as_of": now,
        "rows": rows,
        "budget": {
            "quota_bytes": budget["quota_bytes"],
            "used_declared_bytes": budget["used_declared_bytes"],
            "free_logical_bytes": budget["free_logical_bytes"],
            "reserve_bytes": RESERVE_BYTES,
            "retention_days": budget["retention_days"],
            "future_samples_estimate_bytes": samples,
            "future_bounded_ceiling_bytes": bounded_cost if bounded_known else None,
            "projected_existing_collection_bytes": projected,
            "fits_existing_collection_scenario": (
                budget["used_declared_bytes"] + RESERVE_BYTES + projected <= budget["quota_bytes"]
                if projected is not None
                else None
            ),
            "additional_collection_approved": False,
            "other_collectors_growth_known": False,
            "future_expiry_credited_bytes": 0,
            "disk_free_space_measured": False,
        },
        "read_only": True,
        "payload_integrity_verified": False,
        "execution_performed": False,
    }


def report():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return plan(timezone.now())
