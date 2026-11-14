"""Resumable BDL OGC API Features scans: contiguous ids pinned by a fresh count."""

import hashlib
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ingestion.bdl_catalogue import ORIGIN
from ingestion.bdl_probe import LICENSE, read, validate_api
from ingestion.bdl_samples import capacity
from ingestion.data_paging import digest
from ingestion.data_service import store_snapshot
from ingestion.models import DataHarvest
from ingestion.pig_data import validate_geometry
from ingestion.transport import FetchError
from ingestion.worker_stop import requested as stop_requested

# Collection ids as listed by /collections?f=json on 2026-10-15 (20 collections).
REGIONS = (
    "Bialystok",
    "Gdansk",
    "Katowice",
    "Krakow",
    "Krosno",
    "Lodz",
    "Lublin",
    "Olsztyn",
    "Pila",
    "Poznan",
    "Radom",
    "Szczecin",
    "Szczecinek",
    "Torun",
    "Warszawa",
    "Wroclaw",
    "Zielona_Gora",
)
# Geometry layers keep pages small; subcompartment inventory is attributes only.
# PC audit 2026-10-15: pages of 10 districts / 50 ranges exceeded the 5 MiB budget.
PROFILES = {
    "nadlesnictwa": {"limit": 1, "geometry": True, "max_count": 1000},
    "lesnictwa": {"limit": 5, "geometry": True, "max_count": 20000},
    **{
        f"RDLP_{region}_wydzielenia": {"limit": 1000, "geometry": False, "max_count": 1000000}
        for region in REGIONS
    },
}
INTERVAL_MINUTES = 30 * 24 * 60
PAGES_PER_CALL = 2
ATTRIBUTION = (
    "Bank Danych o Lasach — OGC API Features, deklarowana CC BY4.0. Przebieg stronicowany "
    "po kolejnych id 1..N według świeżej liczby obiektów; oryginalne odpowiedzi bez zmian. "
    "Dane poglądowe, nie urzędowe. Complete potwierdza odczyt wszystkich id przy stałej "
    "liczbie i roku danych, nie jednoczasową wersję u dostawcy."
)


def slug(collection):
    return "bdl-" + collection.lower().replace("_", "-") + "-scan"


def config(collection):
    p = PROFILES[collection]
    return {
        "mode": "bdl_features_scan",
        "pagination": "bdl_offset",
        "collection": collection,
        "feature_limit": p["limit"],
        "geometry": p["geometry"],
    }


def channel_url(collection):
    return ORIGIN + f"/collections/{collection}/items"


def validate_profile(channel):
    collection = channel.config.get("collection")
    if (
        collection not in PROFILES
        or channel.config != config(collection)
        or channel.url != channel_url(collection)
        or channel.kind != "geojson"
        or channel.region != "PL"
        or channel.registry.registry_id != 39
    ):
        raise FetchError("BDL scan outside reviewed profile")
    return collection, PROFILES[collection]


def page_url(channel, offset, limit):
    collection, p = validate_profile(channel)
    url = channel_url(collection) + f"?f=json&limit={limit}&offset={offset}"
    return url if p["geometry"] else url + "&skipGeometry=true"


def manifest(channel, client):
    """Fresh license/identity check and pinned count/year; no geometry downloaded."""
    collection, p = validate_profile(channel)
    validate_api(client)
    url = channel_url(collection) + "?f=json&limit=1&skipGeometry=true"
    data, _ = read(client, url, max_bytes=256 * 1024)
    features, matched = data.get("features"), data.get("numberMatched")
    if (
        data.get("type") != "FeatureCollection"
        or type(matched) is not int
        or not 1 <= matched <= p["max_count"]
        or not isinstance(features, list)
        or len(features) != 1
        or not isinstance(features[0], dict)
        or features[0].get("id") != 1
        or not isinstance(features[0].get("properties"), dict)
        or type(features[0]["properties"].get("a_year")) is not int
        or not 1900 <= features[0]["properties"]["a_year"] <= 2100
    ):
        raise FetchError("BDL scan count, first id or source year changed")
    year = features[0]["properties"]["a_year"]
    scope = digest([channel.url, channel.kind, channel.region, channel.config, matched, year])
    return {"count": matched, "source_year": year, "scope": scope}


def fetch_page(channel, client, offset, pinned):
    if pinned["count"] - offset < 1:
        raise FetchError("BDL scan cursor outside pinned count")
    url = page_url(channel, offset, validate_profile(channel)[1]["limit"])
    client.max_bytes = min(client.max_bytes, settings.DATA_AUTO_MAX_BYTES)
    data, response = read(client, url, max_bytes=client.max_bytes)
    return response.body, "application/geo+json", page_summary(channel, data, offset, pinned)


def page_summary(channel, data, offset, pinned):
    """The same validation for fetched pages and retained original responses."""
    _, p = validate_profile(channel)
    limit = p["limit"]
    expected = min(limit, pinned["count"] - offset)
    if expected < 1:
        raise FetchError("BDL scan cursor outside pinned count")
    url = page_url(channel, offset, limit)
    features, links = data.get("features"), data.get("links")
    if (
        data.get("type") != "FeatureCollection"
        or not isinstance(features, list)
        or len(features) != expected
        or not isinstance(links, list)
        or any(not isinstance(link, dict) for link in links)
    ):
        raise FetchError("BDL scan page size or schema changed")
    for link in links:
        if link.get("rel") != "next":
            continue
        params = parse_qs(urlsplit(str(link.get("href", ""))).query)
        if offset + expected >= pinned["count"] or params.get("offset") != [str(offset + expected)]:
            raise FetchError("BDL scan next link disagrees with pinned cursor")
    missing_geometry_ids = []
    for position, feature in enumerate(features, start=offset + 1):
        if (
            not isinstance(feature, dict)
            or feature.get("type") != "Feature"
            or type(feature.get("id")) is not int
            or feature["id"] != position
            or not isinstance(feature.get("properties"), dict)
            or feature["properties"].get("a_year") != pinned["source_year"]
        ):
            raise FetchError("BDL scan id sequence or source year changed")
        if p["geometry"]:
            try:
                if "geometry" not in feature:
                    raise FetchError("geometry member missing")
                if feature["geometry"] is None:
                    missing_geometry_ids.append(position)
                else:
                    validate_geometry(feature["geometry"])
            except FetchError as exc:
                raise FetchError(
                    f"BDL {channel.config['collection']} feature {position}: {exc}"
                ) from exc
        elif feature.get("geometry") is not None:
            raise FetchError("BDL attribute scan returned geometry")
    summary = {
        "kind": "geojson",
        "scope": "PL",
        "collector": "bdl_features_scan",
        "collection": channel.config["collection"],
        "records": expected,
        "raw_records": expected,
        "partial": True,
        "first_id": offset + 1,
        "last_id": offset + expected,
        "source_count": pinned["count"],
        "source_years": [pinned["source_year"]],
        "identity_scope_hash": pinned["scope"],
        "request_url": url,
        "geometry_requested": p["geometry"],
        "geometry_included": p["geometry"] and not missing_geometry_ids,
        "declared_api_license": LICENSE,
        "original_response_preserved": True,
        "source_publication_time": None,
        "provider_snapshot_consistency_verified": False,
        "retention_basis": "local_copy_age",
    }
    if p["geometry"]:
        summary.update(
            geometry_policy="bdl-null-reported-v1",
            geometry_present_records=expected - len(missing_geometry_ids),
            geometry_missing_records=len(missing_geometry_ids),
            geometry_missing_ids=missing_geometry_ids,
        )
    return summary


def probe(channel, client):
    pinned = manifest(channel, client)
    result = fetch_page(channel, client, 0, pinned)
    if manifest(channel, client)["scope"] != pinned["scope"]:
        raise FetchError("BDL count or year changed during probe; retry audit")
    return result


def advance(channel, client):
    """Caller holds batch lock. HTTP outside transactions; each page commits with cursor."""
    harvest = channel.harvests.filter(status__in=["running", "failed"]).first()
    try:
        _, p = validate_profile(channel)
        pinned = manifest(channel, client)
        if harvest and harvest.config_hash != pinned["scope"]:
            raise FetchError("BDL count or year changed; cursor preserved for review")
        if not harvest:
            url = page_url(channel, 0, p["limit"])
            harvest = DataHarvest.objects.create(
                channel=channel, config_hash=pinned["scope"], start_url=url, next_url=url
            )
        for _ in range(PAGES_PER_CALL):
            if stop_requested():
                break
            offset = harvest.records
            if harvest.next_url != page_url(channel, offset, p["limit"]):
                raise FetchError("BDL scan cursor outside pinned sequence")
            payload, media, summary = fetch_page(channel, client, offset, pinned)
            end = offset + summary["records"]
            if end >= pinned["count"] and manifest(channel, client)["scope"] != pinned["scope"]:
                raise FetchError("BDL count or year changed before completion")
            capacity(len(payload))
            next_url = page_url(channel, end, p["limit"]) if end < pinned["count"] else ""
            summary.update(harvest_id=harvest.pk, page=harvest.pages + 1)
            with transaction.atomic():
                store_snapshot(channel, payload, media, summary, deduplicate=False)
                harvest.pages += 1
                harvest.records = end
                harvest.size_bytes += len(payload)
                harvest.visited_urls.append(digest(summary["request_url"]))
                harvest.page_fingerprints.append(hashlib.sha256(payload).hexdigest())
                harvest.next_url = next_url
                harvest.status = "running" if next_url else "complete"
                harvest.error = ""
                harvest.finished_at = None if next_url else timezone.now()
                harvest.save()
            if not next_url:
                break
    except Exception as exc:
        if harvest:
            harvest.refresh_from_db()
            harvest.status = "failed"
            harvest.error = str(exc)[:300] if isinstance(exc, FetchError) else type(exc).__name__
            harvest.save(update_fields=["status", "error"])
        raise
    return {
        "channel": channel.slug,
        "status": "collecting" if harvest.next_url else "complete",
        "harvest_id": harvest.pk,
        "pages": harvest.pages,
        "records": harvest.records,
        "source_count": pinned["count"],
        "bytes": harvest.size_bytes,
    }
