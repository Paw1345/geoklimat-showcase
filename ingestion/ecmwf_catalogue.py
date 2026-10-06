"""Public ECMWF Data Store collection metadata; no retrieval or account access."""

import hashlib
import json
import math
import re

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ingestion.adapters import Entry, safe_entry, text
from ingestion.transport import FetchError

URL = "https://ecds.ecmwf.int/api/catalogue/v1/collections"
SLUG = "registry-203-ecmwf-ecds"
CONFIG = {"mode": "ecmwf_ecds_collections", "strict_endpoint": True}
EVIDENCE = "https://ecds.ecmwf.int/how-to-api"
NOTE = (
    "Publiczne metadane kolekcji ECMWF Data Store, nie cały katalog ECMWF. "
    "Bez plików prognoz/reanaliz, żądań retrieval, kont i akceptacji warunków. "
    "Zasięg globalny, nie pomiary PL/UE ani historia30dni. "
    "Daty published/updated dotyczą wpisów katalogowych, nie czasu prognozy."
)
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 100


def validate_profile(source):
    if (source.adapter, source.url, source.config) != ("stac", URL, CONFIG):
        raise FetchError("ECMWF reviewed catalogue profile changed")


def fail():
    raise FetchError("ECMWF catalogue schema, identity or completeness changed")


def string(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail()
    return value


def timestamp(value):
    if value is None:
        return ""
    string(value, 50)
    try:
        parsed = parse_datetime(value)
    except ValueError:
        fail()
    if parsed is None or timezone.is_naive(parsed):
        fail()
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Non-finite JSON")


def extent(value):
    if not isinstance(value, dict):
        fail()
    spatial, temporal = value.get("spatial"), value.get("temporal")
    if not isinstance(spatial, dict) or not isinstance(temporal, dict):
        fail()
    boxes, intervals = spatial.get("bbox"), temporal.get("interval")
    if not isinstance(boxes, list) or not 1 <= len(boxes) <= 32:
        fail()
    for box in boxes:
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in box
            )
        ):
            fail()
        west, south, east, north = box
        if not (-180 <= west <= east <= 360 and east - west <= 360 and -90 <= south <= north <= 90):
            fail()
    if not isinstance(intervals, list) or not 1 <= len(intervals) <= 32:
        fail()
    for interval in intervals:
        if not isinstance(interval, list) or len(interval) != 2:
            fail()
        start, end = (timestamp(v) for v in interval)
        if start and end and parse_datetime(start) > parse_datetime(end):
            fail()
    # Preserve 0..360 provider coordinates and future bounds, never infer a mask/current time.
    return {"spatial": {"bbox": boxes}, "temporal": {"interval": intervals}}


def check_links(links, expected):
    if (
        not isinstance(links, list)
        or len(links) > 50
        or any(not isinstance(link, dict) for link in links)
    ):
        fail()
    if any(link.get("rel") in {"next", "previous"} for link in links):
        fail()
    selves = [link for link in links if link.get("rel") == "self"]
    if len(selves) != 1 or selves[0].get("href") != expected:
        fail()


def parse_catalogue(response):
    if response.status != 200 or response.url != URL or len(response.body) > MAX_BYTES:
        fail()
    try:
        data = json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, UnicodeError) as exc:
        raise FetchError("ECMWF catalogue is not strict UTF8 JSON") from exc
    if not isinstance(data, dict):
        fail()
    rows, matched, returned = (
        data.get("collections"),
        data.get("numberMatched"),
        data.get("numberReturned"),
    )
    if (
        not isinstance(rows, list)
        or type(matched) is not int
        or type(returned) is not int
        or not 1 <= matched <= MAX_ROWS
        or matched != returned
        or returned != len(rows)
    ):
        fail()
    check_links(data.get("links"), URL)
    entries, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            fail()
        key = string(row.get("id"), 200)
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9-]*", key)
            or key in seen
            or row.get("type") != "Collection"
            or row.get("stac_version") != "1.1.0"
        ):
            fail()
        seen.add(key)
        target = URL + "/" + key
        check_links(row.get("links"), target)
        title = text(string(row.get("title"), 1000))
        if not title:
            fail()
        license_raw = string(row.get("license"), 100)
        if license_raw != "other":
            fail()
        doi = row.get("sci:doi")
        if doi is not None and not re.fullmatch(r"10\.\d{4,9}/[!-~]+", string(doi, 200)):
            fail()
        keywords = row.get("keywords", [])
        if not isinstance(keywords, list) or len(keywords) > 100:
            fail()
        keywords = [string(v, 200) for v in keywords]
        disabled = row.get("cads:disabled_reason")
        if disabled is not None:
            disabled = string(disabled, 500)
        published, updated = timestamp(row.get("published")), timestamp(row.get("updated"))
        entry = safe_entry(
            Entry(
                key=key,
                title=title,
                url=target,
                published=published,
                updated=updated,
                metadata={
                    "record_kind": "dataset_collection",
                    "listing_scope": "ecmwf_ecds_collections",
                    "collection_id": key,
                    "stac_version": "1.1.0",
                    "license_raw": license_raw,
                    "doi_raw": doi,
                    "keywords_raw": keywords,
                    "extent_raw": extent(row.get("extent")),
                    "disabled_reason_raw": disabled,
                    "catalogue_published_raw": published,
                    "catalogue_updated_raw": updated,
                    "attribution": "ECMWF Data Store",
                    "download_conditions_url": EVIDENCE,
                    "resources_downloaded": False,
                    "terms_accepted": False,
                    "full_ecmwf_catalogue_verified": False,
                    "pl_eu_measurements_verified": False,
                    "exact_geographic_mask_verified": False,
                    "source_description_stored": False,
                },
            ),
            URL,
        )
        if entry is None:
            fail()
        entries.append(entry)
    return entries


def fetch_catalogue(source, client):
    validate_profile(source)
    previous_cap = client.max_bytes
    client.max_bytes = min(previous_cap, MAX_BYTES)
    try:
        response = client.get(URL, {"Accept": "application/json"})
        entries = parse_catalogue(response)
    finally:
        client.max_bytes = previous_cap
    return entries, 0, response, hashlib.sha256(response.body).hexdigest()
