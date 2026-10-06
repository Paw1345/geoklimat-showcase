"""One original BDL page per collection/UTC day; separate from a full harvest."""

import hashlib
from datetime import UTC, timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from ingestion.bdl_probe import COLLECTIONS, LICENSE, MAX_BYTES, fetch_sample, plan
from ingestion.models import DataChannel, DataSnapshot
from ingestion.observations import retention_window
from ingestion.transport import FetchError

RESERVE = 1024**3
ATTRIBUTION = (
    "Bank Danych o Lasach — OGC API Features, deklarowana CC BY4.0. "
    "Oryginalna odpowiedź bez przetwarzania; pojedyncza próbka, dane poglądowe, "
    "nie dokument urzędowy. Rok danych i czas pobrania zapisane w kopii. "
    "Warunki trwałego wykorzystania wymagają odrębnej weryfikacji przed aktywacją."
)


def slug(collection):
    return "bdl-" + collection + "-raw-sample"


def config(collection):
    return {"mode": "bdl_raw_sample", "collection": collection, "offset": 0, "limit": 1}


def validate_profile(channel, approved=False):
    collection = channel.config.get("collection")
    if (
        collection not in COLLECTIONS
        or channel.slug != slug(collection)
        or channel.config != config(collection)
        or channel.url != plan(collection)["url"]
        or channel.kind != "geojson"
        or channel.region != "PL"
        or channel.registry.registry_id != 39
        or channel.interval_minutes != 1440
        or channel.attribution != ATTRIBUTION
        or (approved and not (channel.enabled and channel.access_approved))
    ):
        raise FetchError("BDL sample profile or approval changed")
    return collection


def today(channel, now):
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return channel.snapshots.filter(
        fetched_at__gte=start, fetched_at__lt=start + timedelta(days=1)
    ).defer("payload")


def capacity(extra):
    used = DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
    if used + extra + RESERVE > settings.DATA_STORAGE_MAX_BYTES:
        raise FetchError("BDL cache reserve reached; no copy stored")


def run_sample(channel, client, dry_run=False):
    collection = validate_profile(channel, approved=not dry_run)
    existing = today(channel, timezone.now()).first()
    if existing and not dry_run:
        return {"channel": channel.slug, "status": "daily_limit", "snapshot_id": existing.pk}
    capacity(MAX_BYTES)
    payload, report = fetch_sample(collection, client)
    if len(payload) > min(MAX_BYTES, settings.DATA_AUTO_MAX_BYTES):
        raise FetchError("BDL original copy exceeds automatic budget")
    summary = {
        **report,
        "kind": "geojson",
        "scope": "PL",
        "records": report["number_returned"],
        "partial": True,
        "acquisition_mode": "bdl_raw_sample",
        "original_response_preserved": True,
        "declared_api_license": LICENSE,
        "source_publication_time": None,
    }
    if dry_run:
        return {"channel": channel.slug, "status": "dry_run", "bytes": len(payload), **summary}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(726043)")
        current = (
            DataChannel.objects.select_for_update().select_related("registry").get(pk=channel.pk)
        )
        validate_profile(current, approved=True)
        now = timezone.now()
        existing = today(current, now).first()
        if existing:
            return {"channel": current.slug, "status": "daily_limit", "snapshot_id": existing.pk}
        capacity(len(payload))
        summary["acquired_at"] = now.isoformat()
        summary["database_writes"] = True
        summary["automatic_collection_enabled"] = True
        snapshot = DataSnapshot.objects.create(
            channel=current,
            fetched_at=now,
            expires_at=now + retention_window(),
            payload=payload,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type="application/geo+json",
            summary=summary,
        )
        current.last_status = "stored"
        current.last_error = ""
        current.checked_at = now
        current.next_fetch_at = now + timedelta(days=1)
        current.save(update_fields=["last_status", "last_error", "checked_at", "next_fetch_at"])
        return {
            "channel": current.slug,
            "status": "stored",
            "snapshot_id": snapshot.pk,
            "bytes": len(payload),
            "records": report["number_returned"],
            "original_response_preserved": True,
            "full_dataset_coverage": False,
        }
