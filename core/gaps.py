"""Read-only view of collection gaps: last success and lateness per source and channel."""

from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.models import DataChannel, IngestionRun, Source

WINDOW_DAYS = 14
LATE_INTERVALS = 2
SUCCESS = ("success", "partial", "not_modified")


def _minutes(delta):
    return int(delta.total_seconds() // 60)


def _longest_gaps(source_ids, now):
    """Longest pause between consecutive successful runs in the window, per source."""
    start = now - timedelta(days=WINDOW_DAYS)
    runs = (
        IngestionRun.objects.filter(
            source_id__in=source_ids, status__in=SUCCESS, started_at__gte=start
        )
        .order_by("source_id", "started_at")
        .values_list("source_id", "started_at")
    )
    longest, previous = {}, {}
    for source_id, started_at in runs.iterator():
        gap = started_at - previous.get(source_id, start)
        longest[source_id] = max(longest.get(source_id, gap), gap)
        previous[source_id] = started_at
    for source_id in source_ids:
        gap = now - previous.get(source_id, start)
        longest[source_id] = max(longest.get(source_id, gap), gap)
    return longest


def report(now=None):
    now = now or timezone.now()
    sources = list(
        Source.objects.filter(enabled=True).only(
            "name", "organization", "interval_minutes", "last_success_at", "failures"
        )
    )
    longest = _longest_gaps([source.pk for source in sources], now)
    source_rows = []
    for source in sources:
        allowed = timedelta(minutes=source.interval_minutes * LATE_INTERVALS)
        since = now - source.last_success_at if source.last_success_at else None
        source_rows.append(
            {
                "id": source.pk,
                "name": source.name,
                "organization": source.organization,
                "interval_minutes": source.interval_minutes,
                "last_success_at": (
                    source.last_success_at.isoformat() if source.last_success_at else None
                ),
                "minutes_since_success": _minutes(since) if since is not None else None,
                "longest_gap_minutes": _minutes(longest[source.pk]),
                "failures": source.failures,
                "late": since is None or since > allowed,
            }
        )
    channel_rows = []
    for channel in DataChannel.objects.filter(enabled=True).only(
        "name", "interval_minutes", "last_status", "checked_at"
    ):
        allowed = timedelta(minutes=channel.interval_minutes * LATE_INTERVALS)
        since = now - channel.checked_at if channel.checked_at else None
        channel_rows.append(
            {
                "id": channel.pk,
                "name": channel.name,
                "interval_minutes": channel.interval_minutes,
                "checked_at": channel.checked_at.isoformat() if channel.checked_at else None,
                "minutes_since_check": _minutes(since) if since is not None else None,
                "last_status": channel.last_status,
                "late": since is None or since > allowed or channel.last_status == "failed",
            }
        )
    for rows in (source_rows, channel_rows):
        rows.sort(key=lambda row: (not row["late"], row["name"]))
    return {
        "generated_at": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "late_intervals": LATE_INTERVALS,
        "sources_late": sum(row["late"] for row in source_rows),
        "channels_late": sum(row["late"] for row in channel_rows),
        "sources": source_rows,
        "channels": channel_rows,
    }


@require_GET
@staff_member_required(login_url="login")
@never_cache
def dashboard(request):
    data = report()
    if request.GET.get("format") == "json":
        return JsonResponse(data)
    return render(request, "gaps.html", {"report": data})
