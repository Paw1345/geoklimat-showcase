"""Public CLMS collection descriptions hosted on CDSE; no items or assets."""

import hashlib
import json
import math
import re
from urllib.parse import parse_qsl, urlsplit

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ingestion.adapters import Entry, safe_entry, text
from ingestion.transport import FetchError

ORIGIN = "https://stac.dataspace.copernicus.eu"
PATH = "/v1/collections"
URL = ORIGIN + PATH + "?q=clms&limit=100"
SLUG = "registry-192-clms-cdse"
CONFIG = {"mode": "clms_cdse_collections", "strict_endpoint": True}
POLICY = "https://land.copernicus.eu/en/data-policy"
EVIDENCE = "https://documentation.dataspace.copernicus.eu/Data/ComplementaryData/CLMS.html"
ATTRIBUTION = "European Union's Copernicus Land Monitoring Service information; CDSE"
NOTE = (
    "CLMS: opisy kolekcji udostępnionych w CDSE, wyszukiwanie q=clms. "
    "Metadane globalne i europejskie, nie pobrane dane PL/UE ani pełny katalog CLMS. "
    "Bez scen, rastrów, map, pobierania zasobów i uwierzytelniania. "
    "Formaty/warianty to osobne kolekcje; pełny konektor192 pozostaje otwarty."
)
MAX_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_ROWS = 1000
PAGE_SIZE = 100


def validate_profile(source):
    if (source.adapter, source.url, source.config) != ("stac", URL, CONFIG):
        raise FetchError("CLMS reviewed profile changed")


def page_url(offset):
    return URL + (f"&offset={offset}" if offset else "")


def validate_page_url(value, offset):
    if not isinstance(value, str):
        raise FetchError("CLMS page URL missing")
    parsed = urlsplit(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    params = dict(pairs)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "stac.dataspace.copernicus.eu"
        or parsed.path != PATH
        or parsed.fragment
        or len(pairs) != len(params)
        or params.get("q") != "clms"
        or params.get("limit") != str(PAGE_SIZE)
        or params.get("offset", "0") != str(offset)
        or set(params) not in ({"q", "limit"}, {"q", "limit", "offset"})
        or (offset and "offset" not in params)
    ):
        raise FetchError("CLMS pagination left reviewed query or endpoint")


def unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("Duplicate CLMS JSON key")
        data[key] = value
    return data


def reject_constant(value):
    raise ValueError("Non-finite CLMS JSON")


def string(value, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise FetchError("CLMS text schema changed")
    return value


def strings(value, maximum=100):
    if not isinstance(value, list) or len(value) > maximum:
        raise FetchError("CLMS list schema changed")
    return [string(item, 200) for item in value]


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise FetchError("CLMS non-finite or non-numeric extent/resolution")
    return value


def timestamp(value):
    if value is None:
        return None
    string(value, 40)
    try:
        parsed = parse_datetime(value)
    except ValueError as exc:
        raise FetchError("CLMS invalid extent timestamp") from exc
    if parsed is None or timezone.is_naive(parsed):
        raise FetchError("CLMS extent timestamp requires calendar time and offset")
    return parsed


def extent(row):
    data = row.get("extent")
    if not isinstance(data, dict):
        raise FetchError("CLMS extent missing")
    spatial, temporal = data.get("spatial"), data.get("temporal")
    if not isinstance(spatial, dict) or not isinstance(temporal, dict):
        raise FetchError("CLMS extent schema changed")
    boxes, intervals = spatial.get("bbox"), temporal.get("interval")
    if not isinstance(boxes, list) or not 1 <= len(boxes) <= 32:
        raise FetchError("CLMS spatial extent missing or exceeds budget")
    for box in boxes:
        if not isinstance(box, list) or len(box) != 4:
            raise FetchError("CLMS only reviewed 2D bounding boxes supported")
        west, south, east, north = [number(v) for v in box]
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise FetchError("CLMS extent outside longitude/latitude bounds")
    if not isinstance(intervals, list) or not 1 <= len(intervals) <= 32:
        raise FetchError("CLMS temporal extent missing or exceeds budget")
    for interval in intervals:
        if not isinstance(interval, list) or len(interval) != 2:
            raise FetchError("CLMS temporal interval schema changed")
        start, end = [timestamp(v) for v in interval]
        if start is not None and end is not None and start > end:
            raise FetchError("CLMS temporal extent reversed")
    return {"spatial": {"bbox": boxes}, "temporal": {"interval": intervals}}


def parse_collection(row):
    if not isinstance(row, dict):
        raise FetchError("CLMS collection schema changed")
    key = string(row.get("id"), 200)
    if not re.fullmatch(r"clms_[A-Za-z0-9_.-]+", key):
        raise FetchError("CLMS query returned a non-CLMS collection identity")
    if row.get("type") != "Collection" or row.get("stac_version") != "1.1.0":
        raise FetchError("CLMS reviewed STAC collection type/version changed")
    title = text(string(row.get("title"), 2000))[:500]
    if not title:
        raise FetchError("CLMS readable title missing")
    links = row.get("links")
    if (
        not isinstance(links, list)
        or len(links) > 50
        or any(not isinstance(link, dict) for link in links)
    ):
        raise FetchError("CLMS collection links invalid")
    canonical = ORIGIN + PATH + "/" + key
    selves = [link for link in links if link.get("rel") == "self"]
    licenses = [link for link in links if link.get("rel") == "license"]
    if len(selves) != 1 or selves[0].get("href") != canonical:
        raise FetchError("CLMS collection identity/self mismatch")
    if len(licenses) != 1 or licenses[0].get("href") != POLICY:
        raise FetchError("CLMS source data-policy reference changed")
    license_raw = string(row.get("license"), 100)
    if license_raw not in {"other", "proprietary"}:
        raise FetchError("CLMS reviewed license indicator changed")
    doi = row.get("sci:doi")
    if doi is not None and not re.fullmatch(r"10\.\d{4,9}/[^\s<>\"\x00-\x1f]+", string(doi, 200)):
        raise FetchError("CLMS DOI schema changed")
    summaries = row.get("summaries")
    if not isinstance(summaries, dict):
        raise FetchError("CLMS summaries missing")
    selected = {}
    for field in (
        "proj:code",
        "instruments",
        "constellation",
        "product:type",
        "processing:level",
        "processing:version",
        "product:timeliness_category",
    ):
        if field in summaries:
            selected[field] = strings(summaries[field])
    if "gsd" in summaries:
        values = summaries["gsd"]
        if not isinstance(values, list) or len(values) > 32 or any(number(v) <= 0 for v in values):
            raise FetchError("CLMS ground resolution invalid")
        selected["gsd"] = values
    entry = Entry(
        key=key,
        title=title,
        url=canonical,
        metadata={
            "record_kind": "dataset_collection",
            "listing_scope": "clms_cdse_collections",
            "collection_id": key,
            "stac_version": row["stac_version"],
            "license_raw": license_raw,
            "license_url": POLICY,
            "doi_raw": doi,
            "extent_raw": extent(row),
            "summaries_raw": selected,
            "keywords_raw": strings(row.get("keywords")),
            "attribution": ATTRIBUTION,
            "resources_downloaded": False,
            "full_clms_catalogue_verified": False,
            "pl_eu_measurements_verified": False,
            "exact_geographic_mask_verified": False,
            "source_description_stored": False,
        },
    )
    normalized = safe_entry(entry, URL)
    if normalized is None:
        raise FetchError("CLMS entry cannot fit the public item contract")
    return normalized


def parse_page(response, offset, expected_total=None):
    if response.status != 200 or len(response.body) > MAX_BYTES:
        raise FetchError("CLMS page status or byte budget invalid")
    validate_page_url(response.url, offset)
    try:
        data = json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, UnicodeError) as exc:
        raise FetchError("CLMS page JSON invalid") from exc
    if not isinstance(data, dict):
        raise FetchError("CLMS page object missing")
    total, returned, rows = (
        data.get("numberMatched"),
        data.get("numberReturned"),
        data.get("collections"),
    )
    if (
        type(total) is not int
        or not 0 < total <= MAX_ROWS
        or (expected_total is not None and total != expected_total)
        or not isinstance(rows, list)
        or type(returned) is not int
        or returned != len(rows)
        or len(rows) != min(PAGE_SIZE, total - offset)
    ):
        raise FetchError("CLMS total/page count missing, changed or inconsistent")
    links = data.get("links")
    if (
        not isinstance(links, list)
        or len(links) > 10
        or any(not isinstance(link, dict) for link in links)
    ):
        raise FetchError("CLMS page links invalid")
    selves = [link for link in links if link.get("rel") == "self"]
    nexts = [link for link in links if link.get("rel") == "next"]
    previous = [link for link in links if link.get("rel") == "previous"]
    if len(selves) != 1 or len(nexts) != int(offset + len(rows) < total) or len(previous) > 1:
        raise FetchError("CLMS missing or unexpected page links")
    validate_page_url(selves[0].get("href"), offset)
    if nexts:
        if nexts[0].get("method", "GET") != "GET":
            raise FetchError("CLMS only GET pagination supported")
        validate_page_url(nexts[0].get("href"), offset + len(rows))
    if previous:
        if offset == 0:
            raise FetchError("CLMS unexpected previous page")
        validate_page_url(previous[0].get("href"), offset - PAGE_SIZE)
    entries = [parse_collection(row) for row in rows]
    return entries, total, bool(nexts)


def fetch_catalogue(source, client):
    validate_profile(source)
    entries, fingerprints, identities = [], [], set()
    total, previous_key, consumed = None, "", 0
    old_max_bytes = client.max_bytes
    client.max_bytes = min(old_max_bytes, MAX_BYTES)
    try:
        for offset in range(0, MAX_ROWS, PAGE_SIZE):
            response = client.get(page_url(offset), {"Accept": "application/json"})
            consumed += len(response.body)
            if consumed > MAX_TOTAL_BYTES:
                raise FetchError("CLMS total byte budget exceeded; no partial batch")
            batch, total, more = parse_page(response, offset, total)
            for entry in batch:
                raw_id = entry.metadata["collection_id"]
                if entry.key in identities or raw_id <= previous_key:
                    raise FetchError("CLMS repeated or reordered collection identity")
                identities.add(entry.key)
                previous_key = raw_id
            entries.extend(batch)
            fingerprints.append(hashlib.sha256(response.body).hexdigest())
            if not more:
                if len(entries) != total:
                    raise FetchError("CLMS catalogue query incomplete; no partial batch")
                return (
                    entries,
                    0,
                    response,
                    hashlib.sha256("".join(fingerprints).encode()).hexdigest(),
                )
    finally:
        client.max_bytes = old_max_bytes  # The next source keeps its own request budget.
    raise FetchError("CLMS page budget exceeded; no partial batch")
