"""Pinned identity-set MIDAS scans; each exact-ID page and cursor commit together."""

import json
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ingestion.data_paging import digest
from ingestion.data_service import store_snapshot
from ingestion.models import DataHarvest
from ingestion.pig_data import DETAIL_TERMS, LABELS, TERMS, channel_url, read, validate_geometry
from ingestion.transport import FetchError

PAGE_SIZE = 100
ATTRIBUTION = (
    "PIG-PIB / MIDAS. Stronicowany przebieg według przypiętego spisu OBJECTID, "
    "po100 obiektów na kopię. Complete potwierdza odczyt spisu, nie jednoczasową wersję "
    "atrybutów/geometrii. Dostawca przelicza do WGS84; daty zachowane bez interpretacji. "
    "Retencja30dni dotyczy kopii, nie wieku obiektów. Warunki: " + TERMS + " oraz " + DETAIL_TERMS
)


def config(layer):
    return {
        "mode": "pig_midas_scan",
        "pagination": "pig_ids",
        "layer": layer,
        "feature_limit": PAGE_SIZE,
    }


def slug(layer):
    return "pig-midas-" + str(layer) + "-scan"


def validate_profile(channel):
    if channel.config.get("mode") == "pig_spatial_scan":
        from ingestion.pig_spatial import profile

        profile(channel)
        return
    layer = channel.config.get("layer")
    if (
        type(layer) is not int
        or layer not in LABELS
        or channel.kind != "geojson"
        or channel.region != "PL"
        or channel.url != channel_url(layer)
        or channel.config != config(layer)
    ):
        raise FetchError("MIDAS scan outside reviewed profile")


def page_size(channel):
    if channel.config.get("mode") == "pig_spatial_scan":
        from ingestion.pig_spatial import profile

        return profile(channel)["limit"]
    return PAGE_SIZE


def manifest(channel, client):
    validate_profile(channel)
    if channel.config.get("mode") == "pig_spatial_scan":
        from ingestion.pig_spatial import check_schema

        check_schema(channel, client)
    data = read(
        client,
        channel.url + "?" + urlencode({"f": "json", "where": "1=1", "returnIdsOnly": "true"}),
    )
    ids = data.get("objectIds")
    if (
        data.get("objectIdFieldName") != "OBJECTID"
        or not isinstance(ids, list)
        or not ids
        or len(ids) > 100000
        or any(type(i) is not int or not 0 <= i <= 2**53 for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise FetchError("MIDAS identity manifest missing, repeated or outside budget")
    ids = sorted(ids)
    count = read(
        client,
        channel.url + "?" + urlencode({"f": "json", "where": "1=1", "returnCountOnly": "true"}),
    ).get("count")
    if type(count) is not int or count != len(ids):
        raise FetchError("MIDAS manifest does not match reported count")
    return ids, digest([channel.url, channel.kind, channel.region, channel.config, ids])


def page_url(channel, ids):
    return (
        channel.url
        + "?"
        + urlencode(
            {
                "f": "geojson",
                "where": "1=1",
                "objectIds": ",".join(map(str, ids)),
                "outFields": "*",
                "outSR": "4326",
                "returnGeometry": "true",
                "resultRecordCount": page_size(channel),
                "orderByFields": "OBJECTID ASC",
            }
        )
    )


def fetch_page(channel, client, expected, total, scope_hash):
    url = page_url(channel, expected)
    data = read(client, url)
    features = data.get("features")
    if data.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise FetchError("MIDAS scan GeoJSON missing")
    if len(features) != len(expected) or not features or len(features) > page_size(channel):
        raise FetchError("MIDAS scan page missing or extra identities")
    for feature, identity in zip(features, expected, strict=True):
        if (
            not isinstance(feature, dict)
            or feature.get("type") != "Feature"
            or type(feature.get("id")) is not int
            or feature["id"] != identity
            or not isinstance(feature.get("properties"), dict)
            or type(feature["properties"].get("OBJECTID")) is not int
            or feature["properties"]["OBJECTID"] != identity
        ):
            raise FetchError("MIDAS scan identity/order mismatch")
        validate_geometry(feature.get("geometry"))
    if data.get("exceededTransferLimit", False) is not False:
        raise FetchError("MIDAS exact-ID page truncated")
    spatial = channel.config.get("mode") == "pig_spatial_scan"
    if spatial:
        from ingestion.pig_spatial import EVIDENCE, attribution, profile

        p = profile(channel)
    data["geoklimat_provenance"] = {
        "publisher": "PIG-PIB",
        "dataset": p["dataset"] if spatial else "MIDAS",
        "source_url": url,
        "layer": channel.config["layer"],
        "terms_urls": [TERMS] if spatial else [TERMS, DETAIL_TERMS],
        "attribution": attribution(channel.config["profile"]) if spatial else ATTRIBUTION,
        "source_generated_at": None,
        "source_count": total,
        "partial": True,
        "identity_scope_hash": scope_hash,
        "transformation": "exact OBJECTID subset; supplier projection to EPSG:4326",
    }
    if spatial:
        data["geoklimat_provenance"].update(evidence_url=EVIDENCE, scope_note=p["note"])
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("MIDAS scan payload exceeds budget")
    return (
        payload,
        "application/json",
        {
            "kind": "geojson",
            "scope": "PL",
            "collector": "pig_spatial_scan" if spatial else "pig_midas_scan",
            "records": len(features),
            "raw_records": len(features),
            "partial": True,
            "source_count": total,
            "identity_scope_hash": scope_hash,
            "request_url": url,
            "crs": "EPSG:4326",
            "retention_basis": "local_copy_age",
        },
    )


def probe(channel, client):
    ids, scope_hash = manifest(channel, client)
    result = fetch_page(channel, client, ids[: page_size(channel)], len(ids), scope_hash)
    if manifest(channel, client)[1] != scope_hash:
        raise FetchError("MIDAS identity manifest changed; retry audit")
    return result


def advance(channel, client):
    """Caller holds batch lock. ID hash pins resumed coverage without schema changes."""
    harvest = channel.harvests.filter(status__in=["running", "failed"]).first()
    try:
        ids, scope_hash = manifest(channel, client)
        if harvest and harvest.config_hash != scope_hash:
            raise FetchError("MIDAS manifest changed; preserve cursor and review new scan")
        if not harvest:
            url = page_url(channel, ids[: page_size(channel)])
            harvest = DataHarvest.objects.create(
                channel=channel, config_hash=scope_hash, start_url=url, next_url=url
            )
        for _ in range(2):
            if harvest.pages >= 100000 // page_size(channel) or harvest.records >= len(ids):
                raise FetchError("MIDAS scan cursor or page budget inconsistent")
            expected = ids[harvest.records : harvest.records + page_size(channel)]
            if harvest.next_url != page_url(channel, expected):
                raise FetchError("MIDAS cursor outside pinned identity set")
            payload, media, summary = fetch_page(channel, client, expected, len(ids), scope_hash)
            # Pin the entire identity set, including deletions/additions with equal count.
            if manifest(channel, client)[1] != scope_hash:
                raise FetchError("MIDAS identity manifest changed; cursor not advanced")
            end = harvest.records + len(expected)
            next_url = (
                page_url(channel, ids[end : end + page_size(channel)]) if end < len(ids) else ""
            )
            summary.update(harvest_id=harvest.pk, page=harvest.pages + 1)
            with transaction.atomic():
                store_snapshot(channel, payload, media, summary, deduplicate=False)
                harvest.pages += 1
                harvest.records = end
                harvest.size_bytes += len(payload)
                harvest.visited_urls.append(digest(summary["request_url"]))
                harvest.page_fingerprints.append(digest(expected))
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
        "source_count": len(ids),
        "bytes": harvest.size_bytes,
    }
