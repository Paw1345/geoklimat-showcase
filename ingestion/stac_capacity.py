"""Metadata-only incremental storage scenarios; never a collection approval."""

import math
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from ingestion.data_paging import digest
from ingestion.models import DataChannel, DataSnapshot
from ingestion.observations import retention_window
from ingestion.stac_recent import validate_profile
from ingestion.transport import FetchError

MIB = 1024**2


def capacity(now, daily_budget_mib=256, reserve_mib=1024):
    """Caller supplies a read-only consistent transaction; all bytes are metadata."""
    if (
        timezone.is_naive(now)
        or type(daily_budget_mib) is not int
        or not 1 <= daily_budget_mib <= 1024
        or type(reserve_mib) is not int
        or not 0 <= reserve_mib <= 16384
    ):
        raise ValueError("Invalid STAC capacity scenario")
    quota = settings.DATA_STORAGE_MAX_BYTES
    used = DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
    days = retention_window().days
    reserve = reserve_mib * MIB
    proposed = daily_budget_mib * MIB * days
    rows = []
    channels = list(
        DataChannel.objects.filter(kind="stac", config__mode="stac_recent").order_by("pk")[:21]
    )
    if len(channels) > 20:
        raise ValueError("STAC capacity channel limit exceeded")
    for channel in channels:
        active = channel.enabled and channel.access_approved
        state, estimated, ceiling = "unavailable", None, None
        harvest = channel.harvests.defer("page_fingerprints", "visited_urls").first()
        try:
            validate_profile(channel)
            cycles = math.ceil(1440 / max(15, channel.interval_minutes))
            ceiling = cycles * 2 * settings.DATA_AUTO_MAX_BYTES * days if active else 0
            if harvest:
                expected = digest([channel.url, channel.kind, channel.region, channel.config])
                age = now - harvest.started_at
                if (
                    harvest.config_hash != expected
                    or harvest.status not in {"complete", "bounded"}
                    or not 1 <= harvest.pages <= 2
                    or not 0 <= harvest.records <= harvest.pages * 10
                    or not 0 < harvest.size_bytes <= harvest.pages * settings.DATA_AUTO_MAX_BYTES
                ):
                    state = "invalid_or_partial"
                elif (
                    not timedelta(0)
                    <= age
                    <= timedelta(minutes=2 * max(15, channel.interval_minutes))
                ):
                    state = "stale"
                else:
                    state = "empty_sample" if harvest.records == 0 else "observed_sample"
                    estimated = harvest.size_bytes * cycles * days if active else 0
        except (FetchError, TypeError, AttributeError):
            state = "profile_unavailable"
        rows.append(
            {
                "channel_id": channel.pk,
                "active": active,
                "sample_state": state,
                "sample_harvest_id": harvest.pk if harvest else None,
                "sample_bytes": harvest.size_bytes if harvest else None,
                "future_samples_retention_estimate_bytes": estimated,
                "future_samples_retention_ceiling_bytes": ceiling,
                "provider_daily_volume_bytes": None,
            }
        )
    estimates_known = all(
        not row["active"] or row["future_samples_retention_estimate_bytes"] is not None
        for row in rows
    )
    estimates = sum(row["future_samples_retention_estimate_bytes"] or 0 for row in rows)
    free = max(0, quota - used)
    headroom = max(0, quota - used - reserve)
    return {
        "as_of": now,
        "quota_bytes": quota,
        "used_declared_bytes": used,
        "free_logical_bytes": free,
        "over_quota_bytes": max(0, used - quota),
        "retention_days": days,
        "reserve_bytes": reserve,
        "reserve_shortfall_bytes": max(0, reserve - free),
        "additional_daily_budget_bytes": daily_budget_mib * MIB,
        "additional_retention_budget_bytes": proposed,
        "fits_current_headroom": used + reserve + proposed <= quota,
        "max_additional_daily_bytes_before_other_growth": headroom // days,
        "future_samples_retention_estimate_bytes": estimates if estimates_known else None,
        "fits_with_sample_estimate": (
            used + reserve + proposed + estimates <= quota if estimates_known else None
        ),
        "rows": rows,
        "other_collectors_growth_known": False,
        "provider_daily_volume_known": False,
        "payload_integrity_verified": False,
        "disk_free_space_measured": False,
        "activation_approved": False,
        "read_only": True,
    }


def report(daily_budget_mib=256, reserve_mib=1024):
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return capacity(timezone.now(), daily_budget_mib, reserve_mib)
