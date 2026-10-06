"""Manual, pinned RDLP scan. One raw geometry and cursor commit per invocation."""

import hashlib

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from ingestion.bdl_catalogue import ORIGIN
from ingestion.bdl_manifest import URL, fetch_manifest
from ingestion.bdl_probe import LICENSE, MAX_BYTES, read
from ingestion.bdl_samples import capacity
from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.observations import retention_window
from ingestion.pig_data import validate_geometry
from ingestion.transport import FetchError

SLUG = "bdl-rdlp-pinned-scan"
CONFIG = {"mode": "bdl_rdlp_manual_scan", "collection": "rdlp", "limit": 1}
MANUAL_MAX_BYTES = 50 * 1024 * 1024
BLOCK_NOTE = (
    "Response size/time budget reached; cursor preserved. "
    "Review and retry this same identity explicitly on demand."
)
ATTRIBUTION = (
    "Bank Danych o Lasach — OGC API Features, deklarowana CC BY4.0. "
    "Oryginalne odpowiedzi według przypiętych id/roku; dane poglądowe, nie urzędowe. "
    "Komplet id nie potwierdza jednoczasowej wersji geometrii; duże obiekty na żądanie."
)


def defaults(registry):
    return dict(
        registry=registry,
        kind="geojson",
        region="PL",
        url=URL,
        config=CONFIG,
        interval_minutes=1440,
        attribution=ATTRIBUTION,
    )


def validate_profile(channel, approved=False):
    expected = defaults(channel.registry)
    if (
        channel.slug != SLUG
        or any(getattr(channel, key) != value for key, value in expected.items())
        or channel.registry.registry_id != 39
        or channel.enabled
        or (approved and not channel.access_approved)
    ):
        raise FetchError("BDL manual scan profile or approval changed")


def page_url(identity):
    if type(identity) is not int or not 1 <= identity <= 1000000:
        raise FetchError("BDL scan identity outside bounds")
    return ORIGIN + f"/collections/rdlp/items?f=json&limit=1&id={identity}"


def page_budget(on_demand=False):
    return (
        min(MANUAL_MAX_BYTES, settings.DATA_MANUAL_MAX_BYTES)
        if on_demand
        else min(MAX_BYTES, settings.DATA_AUTO_MAX_BYTES)
    )


def fetch_page(client, expected, on_demand=False):
    # A larger ceiling is possible only through the explicit on-demand path.
    client.max_bytes = page_budget(on_demand)
    url = page_url(expected["id"])
    data, response = read(client, url, max_bytes=client.max_bytes)
    features = data.get("features")
    links = data.get("links")
    if (
        len(response.body) > client.max_bytes
        or data.get("type") != "FeatureCollection"
        or type(data.get("numberMatched")) is not int
        or data["numberMatched"] != 1
        or type(data.get("numberReturned")) is not int
        or data["numberReturned"] != 1
        or not isinstance(features, list)
        or len(features) != 1
        or not isinstance(links, list)
        or any(not isinstance(link, dict) for link in links)
    ):
        raise FetchError("BDL scan exact-ID page incomplete")
    selves = [link for link in links if link.get("rel") == "self"]
    if (
        len(selves) != 1
        or selves[0].get("href") != url
        or any(link.get("rel") == "next" for link in links)
    ):
        raise FetchError("BDL scan exact-ID link changed")
    feature = features[0]
    if (
        not isinstance(feature, dict)
        or feature.get("type") != "Feature"
        or type(feature.get("id")) is not int
        or feature["id"] != expected["id"]
        or not isinstance(feature.get("properties"), dict)
        or type(feature["properties"].get("a_year")) is not int
        or feature["properties"]["a_year"] != expected["source_year"]
    ):
        raise FetchError("BDL scan pinned identity/year changed")
    validate_geometry(feature.get("geometry"))
    return response.body, url


def mark_blocked(channel, baseline, scope, url):
    """Persist only diagnostic state; never consume an identity or overwrite drift."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(726043)")
        current = (
            DataChannel.objects.select_for_update().select_related("registry").get(pk=channel.pk)
        )
        validate_profile(current, approved=True)
        latest = current.harvests.select_for_update().first()
        actual = (
            (latest.pk, latest.records, latest.config_hash, latest.next_url) if latest else None
        )
        if actual != baseline:
            raise FetchError("BDL scan cursor changed; blocked state not written")
        if latest is None:
            latest = DataHarvest.objects.create(
                channel=current, config_hash=scope, start_url=url, next_url=url
            )
        latest.status = "blocked"
        latest.error = BLOCK_NOTE
        latest.save(update_fields=["status", "error"])
        current.last_status = "blocked"
        current.last_error = BLOCK_NOTE
        current.checked_at = timezone.now()
        current.save(update_fields=["last_status", "last_error", "checked_at"])
        return {
            "status": "blocked",
            "harvest_id": latest.pk,
            "records": latest.records,
            "request_url": latest.next_url,
            "on_demand_required": True,
            "size_or_time_cause_distinguished": False,
        }


def advance(channel, client, dry_run=True, on_demand=False):
    """Caller holds shared batch lock. No SQL transaction spans provider requests."""
    validate_profile(channel, approved=not dry_run)
    harvest = channel.harvests.first()
    if harvest and harvest.status == "complete":
        return {
            "status": "complete",
            "harvest_id": harvest.pk,
            "records": harvest.records,
            "provider_snapshot_consistency_verified": False,
        }
    if harvest and harvest.status == "blocked" and not on_demand:
        return {
            "status": "blocked",
            "harvest_id": harvest.pk,
            "records": harvest.records,
            "request_url": harvest.next_url,
            "on_demand_required": True,
        }
    capacity(page_budget(on_demand))
    manifest = fetch_manifest(client)
    scope = manifest["scope_sha256"]
    identities = manifest["identity_manifest"]
    offset = harvest.records if harvest else 0
    if harvest and (
        harvest.config_hash != scope
        or harvest.pages != offset
        or not 0 <= offset < len(identities)
        or harvest.next_url != page_url(identities[offset]["id"])
    ):
        raise FetchError("BDL scan manifest/cursor changed; preserve progress for review")
    baseline = (
        (harvest.pk, harvest.records, harvest.config_hash, harvest.next_url) if harvest else None
    )
    expected = identities[offset]
    try:
        payload, url = (
            fetch_page(client, expected, on_demand=True)
            if on_demand
            else fetch_page(client, expected)
        )
    except FetchError as exc:
        # Do not interpret schema, policy or HTTP errors as permission for a larger fetch.
        if str(exc) == "Response exceeds size/time budget" and not dry_run:
            return mark_blocked(channel, baseline, scope, page_url(expected["id"]))
        raise
    if fetch_manifest(client)["scope_sha256"] != scope:
        raise FetchError("BDL scan manifest changed during GET; cursor not advanced")
    if dry_run:
        return {
            "status": "dry_run",
            "identity": expected,
            "bytes": len(payload),
            "records": offset,
            "source_count": len(identities),
            "database_writes": False,
            "on_demand": on_demand,
            "identity_scope_hash": scope,
            "declared_api_license": LICENSE,
            "original_response_preserved": True,
        }
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(726043)")
        current = (
            DataChannel.objects.select_for_update().select_related("registry").get(pk=channel.pk)
        )
        validate_profile(current, approved=True)
        latest = current.harvests.select_for_update().first()
        actual = (
            (latest.pk, latest.records, latest.config_hash, latest.next_url) if latest else None
        )
        if actual != baseline:
            raise FetchError("BDL scan cursor changed concurrently; no copy stored")
        capacity(len(payload))
        now = timezone.now()
        if latest is None:
            latest = DataHarvest.objects.create(
                channel=current, config_hash=scope, start_url=url, next_url=url
            )
        end = offset + 1
        summary = {
            "kind": "geojson",
            "scope": "PL",
            "records": 1,
            "partial": True,
            "acquisition_mode": CONFIG["mode"],
            "collection": "rdlp",
            "url": url,
            "declared_api_license": LICENSE,
            "original_response_preserved": True,
            "source_publication_time": None,
            "acquired_at": now.isoformat(),
            "feature_ids": [expected["id"]],
            "source_years": [expected["source_year"]],
            "harvest_id": latest.pk,
            "page": end,
            "identity_scope_hash": scope,
            "source_count": len(identities),
            "provider_snapshot_consistency_verified": False,
            "full_dataset_coverage": False,
            "on_demand": on_demand,
            "response_budget_bytes": page_budget(on_demand),
        }
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
        latest.pages = latest.records = end
        latest.size_bytes += len(payload)
        latest.visited_urls.append(url)
        latest.page_fingerprints.append(hashlib.sha256(payload).hexdigest())
        latest.next_url = page_url(identities[end]["id"]) if end < len(identities) else ""
        latest.status = "running" if latest.next_url else "complete"
        latest.finished_at = None if latest.next_url else now
        latest.error = ""
        latest.save()
        current.last_status = latest.status
        current.last_error = ""
        current.checked_at = now
        current.save(update_fields=["last_status", "last_error", "checked_at"])
        return {
            "status": latest.status,
            "harvest_id": latest.pk,
            "snapshot_id": snapshot.pk,
            "records": end,
            "source_count": len(identities),
            "bytes": latest.size_bytes,
            "provider_snapshot_consistency_verified": False,
        }
