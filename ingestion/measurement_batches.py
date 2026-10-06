"""Explicit bounded copy batches; signed metadata scope, never a persistent cursor."""

import hashlib
import json
from datetime import datetime

from django.conf import settings
from django.core import signing
from django.db.models import Count, Func, Sum
from django.utils import timezone

from ingestion.observations import retention_window
from ingestion.transport import FetchError

SALT = "geoklimat.measurement-copy-scope.v1"
COPIES_PER_BATCH = 25


def prepare(channel, now, days, page=None, token=""):
    budget = min(50 * 1024 * 1024, settings.DATA_MANUAL_MAX_BYTES)
    anchor, pinned = now, None
    if token:
        try:
            pinned = signing.loads(token, salt=SALT, max_age=3600)
            if (
                set(pinned) != {"channel", "days", "budget", "anchor", "scope"}
                or pinned["channel"] != channel.pk
                or pinned["days"] != days
                or pinned["budget"] != budget
                or page is None
            ):
                raise ValueError
            anchor = datetime.fromisoformat(pinned["anchor"])
            if timezone.is_naive(anchor) or anchor > now:
                raise ValueError
        except (signing.BadSignature, ValueError, TypeError, KeyError) as exc:
            raise FetchError("Measurement copy scope token invalid/expired") from exc
    copies = channel.snapshots.filter(
        download_job__isnull=True,
        expires_at__gt=now,
        fetched_at__gt=now - retention_window(),
        fetched_at__lte=anchor,
    ).annotate(actual_bytes=Func("payload", function="OCTET_LENGTH"))
    totals = copies.aggregate(count=Count("pk"), size=Sum("actual_bytes"))
    if totals["count"] > 2000:
        raise FetchError("Measurement history exceeds copy budget")
    if page is None:
        if (totals["size"] or 0) > budget:
            raise FetchError("Measurement history exceeds read budget")
        return (
            copies,
            {
                "mode": "all",
                "page": 1,
                "pages": 1,
                "token": "",
                "total_copies": totals["count"],
                "total_bytes": totals["size"] or 0,
                "selected_copies": totals["count"],
                "selected_bytes": totals["size"] or 0,
                "all_available_copies_selected": True,
                "anchor": anchor.isoformat(),
            },
            anchor,
        )
    # Metadata only: never fetch an over-budget payload to decide its batch.
    metadata = list(
        copies.values_list(
            "pk",
            "actual_bytes",
            "sha256",
            "fetched_at",
            "expires_at",
            "size_bytes",
            "media_type",
            "summary__observation_timezone",
            "summary__kind",
            "summary__retention_version",
        )
    )
    zones = set()
    for row in metadata:
        if row[7] not in ("UTC", "Europe/Warsaw"):
            raise FetchError("Measurement copy metadata timezone invalid")
        zones.add(row[7])
    if len(zones) > 1:
        raise FetchError("Mixed measurement timezone interpretation")
    scope = hashlib.sha256(
        json.dumps(
            metadata,
            ensure_ascii=False,
            default=lambda value: value.isoformat(),
        ).encode()
    ).hexdigest()
    if pinned and pinned["scope"] != scope:
        raise FetchError("Measurement copy scope changed; start a new review")
    batches, current, size = [], [], 0
    for row in metadata:
        pk, actual_bytes = row[:2]
        if actual_bytes > budget:
            raise FetchError("Single measurement copy exceeds read budget")
        if current and (len(current) == COPIES_PER_BATCH or size + actual_bytes > budget):
            batches.append((current, size))
            current, size = [], 0
        current.append(pk)
        size += actual_bytes
    if current or not batches:
        batches.append((current, size))
    if page > len(batches):
        raise FetchError("Measurement copy page unavailable")
    ids, size = batches[page - 1]
    if not token:
        token = signing.dumps(
            {
                "channel": channel.pk,
                "days": days,
                "budget": budget,
                "anchor": anchor.isoformat(),
                "scope": scope,
            },
            salt=SALT,
            compress=True,
        )
    return (
        copies.filter(pk__in=ids),
        {
            "mode": "batches",
            "page": page,
            "pages": len(batches),
            "token": token,
            "scope_hash": scope,
            "total_copies": totals["count"],
            "total_bytes": totals["size"] or 0,
            "selected_copies": len(ids),
            "selected_bytes": size,
            "all_available_copies_selected": len(batches) == 1,
            "anchor": anchor.isoformat(),
            "has_previous": page > 1,
            "has_next": page < len(batches),
        },
        anchor,
    )
