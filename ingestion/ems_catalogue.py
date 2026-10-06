"""Initial public CEMS activation page; country-name EU filter, no products."""

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlsplit

from django.utils.dateparse import parse_datetime

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

ORIGIN = "https://mapping.emergency.copernicus.eu"
PATH = "/activations/api/activations/"
URL = ORIGIN + PATH + "?limit=20&offset=0"
SLUG = "registry-196-ems-activations"
CONFIG = {
    "mode": "ems_initial_activations",
    "strict_endpoint": True,
    "country_filter": "eu27",
    "terms_version": "cems-2023-06-07-citation-2026-08-20",
    "terms_accepted": False,
}
MAX_BYTES = 512 * 1024
EVIDENCE = ORIGIN + "/about/how-to-harvest-cems-mapping-data/"
TERMS = ORIGIN + "/terms-and-conditions/"
CITATION = ORIGIN + "/about/citation-guidelines/"
NOTE = (
    "EMS: metadane aktywacji z pierwszych20 wpisów publicznego API, wybrane po nazwach "
    "państw UE27 (w tym Polska), nie dokładnej geometrii. Bez pełnego archiwum, "
    "map, rastrów i lokalnych alertów. Pełny konektor196 pozostaje otwarty."
)
# Reviewed against official EU country list 2026-10-04; names, not a geometry mask.
EU27 = frozenset(
    "Austria|Belgium|Bulgaria|Croatia|Cyprus|Czechia|Denmark|Estonia|Finland|France|"
    "Germany|Greece|Hungary|Ireland|Italy|Latvia|Lithuania|Luxembourg|Malta|Netherlands|"
    "Poland|Portugal|Romania|Slovakia|Slovenia|Spain|Sweden".split("|")
)
ALIASES = {"Czech Republic": "Czechia", "The Netherlands": "Netherlands"}


def validate_profile(source):
    accepted = source.config.get("terms_accepted") if isinstance(source.config, dict) else None
    if type(accepted) is not bool or (source.adapter, source.url, source.config) != (
        "json",
        URL,
        {**CONFIG, "terms_accepted": accepted},
    ):
        raise FetchError("EMS reviewed profile changed")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate EMS JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Non-finite EMS JSON")


def string(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FetchError("EMS text schema changed")
    return value


def timestamp(value):
    string(value, 40)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value):
        raise FetchError("EMS timestamp format changed")
    try:
        parsed = parse_datetime(value)
    except ValueError as exc:
        raise FetchError("EMS invalid calendar timestamp") from exc
    if parsed is None:
        raise FetchError("EMS timestamp invalid")
    return value  # Raw activation/update time, never an inferred publication date.


def next_page(value, expected):
    if not expected:
        if value is not None:
            raise FetchError("EMS unexpected next page")
        return
    if not isinstance(value, str):
        raise FetchError("EMS missing next page")
    parsed = urlsplit(value)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc != "mapping.emergency.copernicus.eu"
        or parsed.path != PATH
        or parsed.fragment
        or len(pairs) != 2
        or dict(pairs) != {"limit": "20", "offset": "20"}
    ):
        raise FetchError("EMS next-page scope changed")
    # Publisher emits HTTP here. Validate as inert metadata; NEVER follow/rewrite.


def parse_page(source, response):
    validate_profile(source)
    if response.url != URL or response.status != 200 or len(response.body) > MAX_BYTES:
        raise FetchError("EMS endpoint, response or byte budget changed")
    try:
        data = json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError("EMS invalid JSON") from exc
    if not isinstance(data, dict) or not {"count", "next", "previous", "results"} <= data.keys():
        raise FetchError("EMS page schema changed")
    total, rows = data["count"], data["results"]
    if (
        type(total) is not int
        or not 0 <= total <= 100000
        or not isinstance(rows, list)
        or len(rows) != min(20, total)
        or data["previous"] is not None
    ):
        raise FetchError("EMS count, first page or pagination changed")
    next_page(data["next"], total > 20)
    entries, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise FetchError("EMS activation schema changed")
        code = string(row.get("code"), 12)
        if not re.fullmatch(r"EMS[RN]\d{1,6}", code) or code in seen:
            raise FetchError("EMS activation identity missing or duplicated")
        seen.add(code)
        title = text(string(row.get("name"), 1000))
        if not title:
            raise FetchError("EMS empty activation name")
        countries = row.get("countries")
        if not isinstance(countries, list) or not 1 <= len(countries) <= 30:
            raise FetchError("EMS country schema changed")
        names = []
        for country in countries:
            if not isinstance(country, dict):
                raise FetchError("EMS country name missing")
            name = string(country.get("short_name"), 100)
            if name in names:
                raise FetchError("EMS repeated country")
            names.append(name)
        selected = sorted({ALIASES.get(name, name) for name in names} & EU27)
        category = row.get("category")
        if not isinstance(category, dict):
            raise FetchError("EMS category schema changed")
        slug = string(category.get("slug"), 60)
        label = string(category.get("name"), 100)
        if not re.fullmatch(r"[a-z0-9_-]+", slug):
            raise FetchError("EMS category identity changed")
        phase, closed = row.get("drmPhase"), row.get("closed")
        if phase not in {"response", "recovery", "preparedness"} or type(closed) is not bool:
            raise FetchError("EMS phase or closed flag invalid")
        for key in ("n_aois", "n_products"):
            if type(row.get(key)) is not int or not 0 <= row[key] <= 100000:
                raise FetchError("EMS product/AOI count invalid")
        activation, updated = timestamp(row.get("activationTime")), timestamp(row.get("lastUpdate"))
        # Validate all rows, including non-EU records, before returning any batch.
        if not selected:
            continue
        metadata = {
            "record_kind": "dataset_catalogue",
            "listing_scope": "ems_initial_activations",
            "activation_code": code,
            "countries_raw": sorted(names),
            "matched_eu_countries": selected,
            "category_raw": {"slug": slug, "name": label},
            "drm_phase_raw": phase,
            "closed_raw": closed,
            "activation_time_raw": activation,
            "last_update_raw": updated,
            "n_aois_raw": row["n_aois"],
            "n_products_raw": row["n_products"],
            "initial_page_limit": 20,
            "full_archive_coverage": False,
            "exact_geographic_mask_verified": False,
            "local_alert_verified": False,
            "geometry_downloaded": False,
            "resources_downloaded": False,
            "provider_next_followed": False,
            "evidence_url": EVIDENCE,
            "terms_url": TERMS,
            "citation_guidelines_url": CITATION,
            "attribution": (
                f"Copernicus Emergency Management Service "
                f"(© {activation[:4]} European Union), {code}"
            ),
            "source_prose_ignored": True,
        }
        entries.append(Entry(code, title, ORIGIN + PATH + code + "/", metadata=metadata))
    return entries, total, len(rows)


def fetch_catalogue(source, client, conditional=True):
    validate_profile(source)
    client.max_bytes = MAX_BYTES
    headers = {}
    if conditional and source.etag:
        headers["If-None-Match"] = source.etag
    if conditional and source.last_modified:
        headers["If-Modified-Since"] = source.last_modified
    response = client.get(URL, headers)
    if response.url != URL:
        raise FetchError("EMS response left configured endpoint")
    if response.status == 304:
        return [], 0, response, ""
    entries, _, _ = parse_page(source, response)
    stable = sorted((entry.key, entry.digest()) for entry in entries)
    return entries, 0, response, hashlib.sha256(json.dumps(stable).encode()).hexdigest()
