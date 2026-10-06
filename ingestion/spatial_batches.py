"""Signed, retention-bound local GIS inventory; never a persisted harvesting cursor."""

import hashlib
import json
from datetime import datetime

from django.conf import settings
from django.core import signing
from django.db.models import BigIntegerField, Func
from django.utils import timezone

from ingestion.data_paging import digest
from ingestion.observations import retention_window
from ingestion.transport import FetchError

SALT = "geoklimat.spatial-copy-scope.v1"
COPIES_PER_BATCH = 25
MAX_COPIES = 10000


def profile_hash(channel):
    return digest(
        [
            channel.pk,
            channel.url,
            channel.kind,
            channel.region,
            channel.config,
            channel.access_approved,
            channel.attribution,
        ]
    )


def read_token(token, channel, bbox, limit, now, *, method="envelope"):
    if not token:
        return None
    try:
        pinned = signing.loads(token, salt=SALT, max_age=3600)
        expected = {
            "v",
            "channel",
            "harvest",
            "anchor",
            "max_pk",
            "manifest",
            "profile",
            "bbox",
            "limit",
            "budget",
            "batch_size",
        }
        if isinstance(pinned, dict) and pinned.get("v") == 2:
            expected.add("method")
        if (
            not isinstance(pinned, dict)
            or set(pinned) != expected
            or type(pinned["v"]) is not int
            or pinned["v"] not in {1, 2}
            or (pinned.get("method", "envelope") != method)
            or type(pinned["harvest"]) is not int
            or pinned["harvest"] < 1
            or type(pinned["max_pk"]) is not int
            or pinned["max_pk"] < 1
            or pinned["channel"] != channel.pk
            or pinned["bbox"] != list(bbox)
            or pinned["limit"] != limit
            or pinned["profile"] != profile_hash(channel)
            or pinned["budget"] != min(50 * 1024 * 1024, settings.DATA_MANUAL_MAX_BYTES)
            or pinned["batch_size"] != COPIES_PER_BATCH
        ):
            raise ValueError
        anchor = datetime.fromisoformat(pinned["anchor"])
        if timezone.is_naive(anchor) or anchor > now or (now - anchor).total_seconds() > 3600:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError) as exc:
        raise FetchError("Spatial batch token invalid/expired or filters/profile changed") from exc
    return pinned


def prepare(channel, harvest, now, bbox, limit, page, token, pinned, *, method="envelope"):
    if page > 1 and pinned is None:
        raise FetchError("Start spatial batches at page1 before continuing")
    anchor = datetime.fromisoformat(pinned["anchor"]) if pinned else now
    budget = min(50 * 1024 * 1024, settings.DATA_MANUAL_MAX_BYTES)
    copies = channel.snapshots.filter(
        summary__harvest_id=harvest.pk,
        download_job__isnull=True,
        expires_at__gt=anchor,
        fetched_at__gt=anchor - retention_window(),
        fetched_at__lte=anchor,
    )
    if pinned:
        copies = copies.filter(pk__lte=pinned["max_pk"])
    fields = (
        "pk",
        "sha256",
        "fetched_at",
        "expires_at",
        "size_bytes",
        "media_type",
        "summary__page",
        "summary__records",
        "summary__source_count",
        "summary__identity_scope_hash",
        "summary__collector",
        "actual_bytes",
    )
    rows = list(
        copies.annotate(
            actual_bytes=Func("payload", function="OCTET_LENGTH", output_field=BigIntegerField())
        )
        .order_by("summary__page", "pk")
        .values(*fields)[: MAX_COPIES + 1]
    )
    if not rows or len(rows) > MAX_COPIES:
        raise FetchError("Spatial batch copy inventory unavailable or exceeds budget")
    for row in rows:
        if (
            type(row["actual_bytes"]) is not int
            or not 0 < row["actual_bytes"] <= min(settings.DATA_AUTO_MAX_BYTES, budget)
            or row["actual_bytes"] != row["size_bytes"]
            or row["expires_at"] <= now
            or row["fetched_at"] + retention_window() <= now
        ):
            raise FetchError("Spatial batch copy expired or size metadata inconsistent")
    scope = hashlib.sha256(
        json.dumps(
            [harvest.pk, harvest.config_hash, rows],
            sort_keys=True,
            default=lambda value: value.isoformat(),
        ).encode()
    ).hexdigest()
    if pinned and pinned["manifest"] != scope:
        raise FetchError("Spatial batch inventory changed; start a new review")
    batches, current, size = [], [], 0
    for row in rows:
        if current and (len(current) == COPIES_PER_BATCH or size + row["actual_bytes"] > budget):
            batches.append((current, size))
            current, size = [], 0
        current.append(row["pk"])
        size += row["actual_bytes"]
    batches.append((current, size))
    if page > len(batches):
        raise FetchError("Spatial batch page unavailable")
    ids, size = batches[page - 1]
    if not token:
        token = signing.dumps(
            {
                "v": 2,
                "method": method,
                "channel": channel.pk,
                "harvest": harvest.pk,
                "anchor": anchor.isoformat(),
                "max_pk": max(row["pk"] for row in rows),
                "manifest": scope,
                "profile": profile_hash(channel),
                "bbox": list(bbox),
                "limit": limit,
                "budget": budget,
                "batch_size": COPIES_PER_BATCH,
            },
            salt=SALT,
            compress=True,
        )
    return ids, {
        "mode": "batches",
        "intersection_method": method,
        "page": page,
        "pages": len(batches),
        "token": token,
        "scope_hash": scope,
        "anchor": anchor.isoformat(),
        "total_copies": len(rows),
        "total_bytes": sum(row["actual_bytes"] for row in rows),
        "selected_copies": len(ids),
        "selected_bytes": size,
        "all_anchored_copies_selected": len(batches) == 1,
        "has_previous": page > 1,
        "has_next": page < len(batches),
        "provider_snapshot_verified": False,
    }
