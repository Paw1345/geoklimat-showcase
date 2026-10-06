"""Consistent, read-only cache accounting, not observation coverage or integrity."""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.db import connection, transaction
from django.db.models import BigIntegerField, Count, F, Func, Max, Min, Q, Sum
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.models import DataChannel, DataSnapshot
from ingestion.observations import retention_window

COUNTERS = (
    "copies",
    "declared_bytes",
    "actual_bytes",
    "expired_copies",
    "expired_bytes",
    "chunk_copies",
    "chunk_bytes",
    "size_mismatches",
)


def report(now=None):
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise ValueError("Aware report time required")
    expired = (
        Q(expires_at__lte=now)
        | Q(fetched_at__lte=now - retention_window())
        | Q(download_job__expires_at__lte=now)
    )
    length = Func(F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField())
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        channels = list(DataChannel.objects.order_by("pk").values("id", "name", "kind")[:1001])
        if len(channels) > 1000:
            raise ValueError("Cache report channel limit exceeded")
        groups = list(
            DataSnapshot.objects.order_by()
            .values("channel_id")
            .annotate(
                copies=Count("pk"),
                declared_bytes=Sum("size_bytes"),
                actual_bytes=Sum(length),
                expired_copies=Count("pk", filter=expired),
                expired_bytes=Sum(length, filter=expired),
                chunk_copies=Count("pk", filter=Q(download_job__isnull=False)),
                chunk_bytes=Sum(length, filter=Q(download_job__isnull=False)),
                size_mismatches=Count("pk", filter=~Q(size_bytes=length)),
                oldest_copy=Min("fetched_at", filter=~expired),
                newest_copy=Max("fetched_at", filter=~expired),
            )
        )
    indexed = {group["channel_id"]: group for group in groups}
    rows = []
    for channel in channels:
        group = indexed.get(channel["id"], {})
        rows.append(
            {
                **channel,
                **{key: group.get(key) or 0 for key in COUNTERS},
                "oldest_copy": group.get("oldest_copy"),
                "newest_copy": group.get("newest_copy"),
            }
        )
    total = {key: sum(row[key] for row in rows) for key in COUNTERS}
    quota = settings.DATA_STORAGE_MAX_BYTES
    return {
        "as_of": now,
        "retention_days": retention_window().days,
        "quota_bytes": quota,
        "total": total,
        "channels": rows,
        "remaining_logical_bytes": max(0, quota - total["declared_bytes"]),
        "over_quota_bytes": max(0, total["declared_bytes"] - quota),
        "observation_coverage_verified": False,
        "payload_hashes_verified": False,
        "disk_free_space_measured": False,
    }


@staff_member_required
@require_GET
@never_cache
def dashboard(request):
    mode = request.GET.get("format", "html")
    if mode not in {"html", "json"}:
        return HttpResponseBadRequest("Nieznany format raportu")
    try:
        result = report()
    except ValueError:
        return HttpResponseBadRequest("Raport przekracza obsługiwany zakres")
    if mode == "json":
        response = JsonResponse(result)
        response["X-Content-Type-Options"] = "nosniff"
        return response
    return render(request, "data_storage.html", {"report": result})
