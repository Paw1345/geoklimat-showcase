"""Disposable one-feature OGC API probe, never a persistent data collector."""

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from ingestion.bdl_catalogue import ORIGIN
from ingestion.pig_data import validate_geometry
from ingestion.transport import FetchError

COLLECTIONS = ("rdlp", "nadlesnictwa", "lesnictwa")
LICENSE = "https://creativecommons.org/licenses/by/4.0/"
OPENAPI = ORIGIN + "/openapi?f=json"
MAX_BYTES = 5 * 1024 * 1024


def plan(collection, offset=0):
    if collection not in COLLECTIONS or type(offset) is not int or not 0 <= offset <= 1000000:
        raise FetchError("BDL probe profile outside bounds")
    return {
        "collection": collection,
        "offset": offset,
        "url": ORIGIN + f"/collections/{collection}/items?f=json&limit=1&offset={offset}",
        "max_response_bytes": MAX_BYTES,
        "max_features": 1,
        "max_feature_pages": 1,
        "database_writes": False,
        "automatic_collection_enabled": False,
        "full_dataset_coverage": False,
        "source_terms_accepted_on_behalf_of_user": False,
        "fetched": False,
    }


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FetchError("BDL duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value):
    raise FetchError("BDL non-finite JSON constant")


def read(client, url, max_bytes=MAX_BYTES):
    if type(max_bytes) is not int or not 0 < max_bytes <= 50 * 1024 * 1024:
        raise FetchError("BDL response budget outside bounds")
    response = client.get(url)
    if response.url != url or response.status != 200 or not 0 < len(response.body) <= max_bytes:
        raise FetchError("BDL response identity or budget changed")
    try:
        data = json.loads(
            response.body, object_pairs_hook=unique_object, parse_constant=invalid_constant
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError("BDL invalid JSON") from exc
    if not isinstance(data, dict):
        raise FetchError("BDL object required")
    return data, response


def validate_api(client):
    api, _ = read(client, OPENAPI)
    info = api.get("info")
    if (
        not isinstance(info, dict)
        or info.get("title") != "Usługa OGC API Features Banku Danych o Lasach"
        or not isinstance(info.get("contact"), dict)
        or not isinstance(info.get("license"), dict)
        or info.get("contact", {}).get("name") != "Bank Danych o Lasach"
        or info.get("license", {}).get("url") != LICENSE
        or info.get("termsOfService") != LICENSE
    ):
        raise FetchError("BDL declared API license or identity changed")


def fetch_sample(collection, client, offset=0):
    result = plan(collection, offset)
    client.max_bytes = min(client.max_bytes, MAX_BYTES)
    validate_api(client)
    data, response = read(client, result["url"])
    features, matched, returned = (
        data.get("features"),
        data.get("numberMatched"),
        data.get("numberReturned"),
    )
    if (
        data.get("type") != "FeatureCollection"
        or not isinstance(features, list)
        or type(matched) is not int
        or not 0 <= matched <= 1000000
        or type(returned) is not int
        or returned != len(features)
        or returned != min(1, max(0, matched - offset))
    ):
        raise FetchError("BDL feature count or schema changed")
    links = data.get("links")
    if not isinstance(links, list) or any(not isinstance(link, dict) for link in links):
        raise FetchError("BDL links missing")
    self_links = [link for link in links if link.get("rel") == "self"]
    # BDL omits the default offset=0 from its canonical self URL.
    canonical_zero = result["url"].removesuffix("&offset=0")
    if len(self_links) != 1 or self_links[0].get("href") not in (
        result["url"],
        canonical_zero if offset == 0 else result["url"],
    ):
        raise FetchError("BDL page self link changed")
    next_links = [link for link in links if link.get("rel") == "next"]
    more = offset + returned < matched
    if len(next_links) != int(more):
        raise FetchError("BDL pagination count changed")
    if more:
        link = next_links[0]
        target = urlsplit(link.get("href", ""))
        params = parse_qs(target.query, keep_blank_values=True)
        if (
            target.scheme != "https"
            or target.netloc != "ogcapi.bdl.lasy.gov.pl"
            or target.path != f"/collections/{collection}/items"
            or target.fragment
            or link.get("method", "GET") != "GET"
            or set(params) not in ({"offset", "limit"}, {"offset", "limit", "f"})
            or params.get("offset") != [str(offset + 1)]
            or params.get("limit") != ["1"]
            or params.get("f", ["json"]) != ["json"]
        ):
            raise FetchError("BDL pagination leaves fixed profile")
    years, geometry_types = [], []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise FetchError("BDL feature required")
        identity, properties = feature.get("id"), feature.get("properties")
        if (
            type(identity) not in (int, str)
            or not str(identity)
            or len(str(identity)) > 128
            or not isinstance(properties, dict)
            or type(properties.get("a_year")) is not int
            or not 1900 <= properties["a_year"] <= 2100
        ):
            raise FetchError("BDL identity or source year changed")
        validate_geometry(feature.get("geometry"))
        years.append(properties["a_year"])
        geometry_types.append(feature["geometry"]["type"])
    report = {
        **result,
        "fetched": True,
        "http": response.status,
        "robots": response.access_policy,
        "declared_api_license": LICENSE,
        "portal_terms_resolved_for_bulk_reuse": False,
        "provider_number_matched": matched,
        "number_returned": returned,
        "response_bytes": len(response.body),
        "response_sha256": hashlib.sha256(response.body).hexdigest(),
        "geometry_types": geometry_types,
        "source_years": years,
        "feature_ids": [feature["id"] for feature in features],
        "next_page_validated_not_fetched": more,
        "cross_page_consistency_verified": False,
        "full_dataset_bytes_estimated": False,
        "source_publication_time_known": False,
        "official_administrative_evidence": False,
        "attribution": "Bank Danych o Lasach — OGC API Features; dane poglądowe.",
    }
    return response.body, report


def probe(collection, client, offset=0):
    return fetch_sample(collection, client, offset)[1]
