"""Disposable LC100 metadata preflight; never an asset downloader or collector."""

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import urlencode
from urllib.request import BaseHandler

from django.conf import settings

from ingestion.clms_catalogue import (
    number,
    parse_collection,
    reject_constant,
    timestamp,
    unique_object,
)
from ingestion.transport import Client, FetchError

COLLECTION_ID = "clms_lc_global_100m_yearly_v3_cog"
COLLECTION_URL = "https://stac.dataspace.copernicus.eu/v1/collections/" + COLLECTION_ID
ITEMS_URL = COLLECTION_URL + "/items"
SEARCH_URL = (
    ITEMS_URL
    + "?"
    + urlencode(
        {
            "limit": 2,
            "bbox": "14,49,24.5,55",
            "datetime": "2019-01-01T00:00:00Z/2019-12-31T23:59:59Z",
        }
    )
)
MAX_BYTES = 2 * 1024 * 1024
PROPERTY_FIELDS = (
    "datetime",
    "start_datetime",
    "end_datetime",
    "created",
    "updated",
    "gsd",
    "proj:code",
    "processing:version",
    "product:type",
)


def plan():
    return {
        "request_urls": [COLLECTION_URL, SEARCH_URL],
        "max_bytes_per_response": MAX_BYTES,
        "max_total_bytes": 2 * MAX_BYTES,
        "fetched": False,
        "database_writes": False,
        "resources_downloaded": False,
        "terms_accepted": False,
        "full_dataset_coverage": False,
        "exact_geographic_mask_verified": False,
        "nodata": None,
        "legend": None,
        "result_state": "not_fetched",
        "query_bbox": [14, 49, 24.5, 55],
        "query_year": 2019,
    }


def decode(response, url):
    if (
        response.status != 200
        or response.url != url
        or not 0 < len(response.body) <= MAX_BYTES
        or response.headers.get("content-type", "").split(";")[0].lower().strip()
        not in {"application/json", "application/geo+json"}
    ):
        raise FetchError("LC100 response identity, media or budget changed")
    try:
        value = json.loads(
            response.body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
            parse_float=lambda v: number(float(v)),
            parse_int=lambda v: number(int(v)),
        )
    except (ValueError, UnicodeError, RecursionError, OverflowError) as exc:
        raise FetchError("LC100 invalid JSON") from exc
    if not isinstance(value, dict):
        raise FetchError("LC100 JSON object required")
    return value


def provenance(response):
    return {
        "request_url": response.url,
        "status": response.status,
        "bytes": len(response.body),
        "sha256": hashlib.sha256(response.body).hexdigest(),
        "access_policy": response.access_policy,
    }


def require_link(data, rel, href):
    links = data.get("links")
    if (
        not isinstance(links, list)
        or len(links) > 50
        or any(not isinstance(v, dict) for v in links)
    ):
        raise FetchError("LC100 links schema changed")
    selected = [v for v in links if v.get("rel") == rel]
    if (
        len(selected) != 1
        or selected[0].get("href") != href
        or selected[0].get("method", "GET") != "GET"
    ):
        raise FetchError("LC100 link identity changed")
    return links


def collection_metadata(response):
    catalog = decode(response, COLLECTION_URL)
    metadata = parse_collection(catalog).metadata
    expected = {
        "gsd": [100],
        "proj:code": ["EPSG:4326"],
        "processing:version": ["v3.1.2"],
        "product:type": ["dynamic_land_cover"],
    }
    if (
        catalog.get("id") != COLLECTION_ID
        or metadata["license_raw"] != "other"
        or any(catalog["summaries"].get(key) != value for key, value in expected.items())
    ):
        raise FetchError("LC100 collection profile changed")
    require_link(catalog, "items", ITEMS_URL)
    require_link(catalog, "self", COLLECTION_URL)
    return metadata


def product_metadata(feature):
    if not isinstance(feature, dict) or any(
        feature.get(key) != value
        for key, value in {
            "id": "PROBAV_LC100_global_v3.1.2_2019_cog",
            "type": "Feature",
            "collection": COLLECTION_ID,
            "stac_version": "1.1.0",
        }.items()
    ):
        raise FetchError("LC100 feature identity changed")
    props, assets, box = feature.get("properties"), feature.get("assets"), feature.get("bbox")
    if not isinstance(props, dict) or not isinstance(assets, dict) or not 2 <= len(assets) <= 32:
        raise FetchError("LC100 feature schema changed")
    # Do not export or interpret the full geometry; bbox is not an administrative mask.
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon" or "license" in feature:
        raise FetchError("LC100 geometry type or license profile changed")
    if not isinstance(box, list) or len(box) != 4:
        raise FetchError("LC100 bbox schema changed")
    west, south, east, north = [number(v) for v in box]
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90) or (
        east < 14 or west > 24.5 or north < 49 or south > 55
    ):
        raise FetchError("LC100 bbox does not intersect reviewed query")
    dates = {key: timestamp(props.get(key)) for key in PROPERTY_FIELDS[:5]}
    start, end = datetime(2019, 1, 1, tzinfo=UTC), datetime(2019, 12, 31, 23, 59, 59, 999999, UTC)
    if (
        dates["datetime"] != start
        or dates["start_datetime"] != start
        or dates["end_datetime"] != end
        or dates["created"] is None
        or dates["updated"] is None
        or dates["created"] > dates["updated"]
        or number(props.get("gsd")) != 100
        or props.get("proj:code") != "EPSG:4326"
        or props.get("processing:version") != "v3.1.2"
        or props.get("product:type") != "dynamic_land_cover"
    ):
        raise FetchError("LC100 annual product profile changed")
    selected_assets = []
    for key, media, roles in (
        ("Product", "application/zip", ["data", "metadata", "archive"]),
        (
            "discrete_classification_map",
            "image/tiff; application=geotiff; profile=cloud-optimized",
            ["data"],
        ),
    ):
        asset = assets.get(key)
        if not isinstance(asset, dict) or asset.get("type") != media or asset.get("roles") != roles:
            raise FetchError("LC100 asset metadata profile changed")
        size = asset.get("file:size")
        if (
            type(size) is not int
            or not 0 < size <= 2**63 - 1
            or (key == "discrete_classification_map" and asset.get("proj:code") != "EPSG:4326")
        ):
            raise FetchError("LC100 asset size or projection changed")
        selected_assets.append({"key": key, "media_type": media, "file_size_bytes": size})
    return {
        "id": feature["id"],
        "bbox_raw": box,
        "properties_raw": {key: props[key] for key in PROPERTY_FIELDS},
        "asset_metadata": selected_assets,
    }


def parse_metadata(collection_response, items_response):
    metadata = collection_metadata(collection_response)
    result = decode(items_response, SEARCH_URL)
    features = result.get("features")
    returned = result.get("numberReturned")
    if (
        result.get("type") != "FeatureCollection"
        or not isinstance(features, list)
        or len(features) > 1
        or type(returned) is not int
        or returned != len(features)
    ):
        raise FetchError("LC100 result profile changed")
    links = require_link(result, "self", ITEMS_URL)
    if any(link.get("rel") in {"next", "previous"} for link in links):
        raise FetchError("LC100 paging profile changed")
    warnings = [
        "historical",
        "not_clipped",
        "full_coverage_unconfirmed",
        "nodata_legend_unconfirmed",
    ]
    product = None
    if features:
        product = product_metadata(features[0])
        warnings.append("global_product")
        if any(
            a["file_size_bytes"] > settings.DATA_MANUAL_MAX_BYTES for a in product["asset_metadata"]
        ):
            warnings.append("asset_exceeds_manual_limit")
    return {
        **plan(),
        "fetched": True,
        "result_state": "found" if product else "no_results",
        "collection": {
            key: metadata[key]
            for key in (
                "collection_id",
                "license_raw",
                "license_url",
                "doi_raw",
                "attribution",
                "extent_raw",
                "summaries_raw",
            )
        },
        "responses": [provenance(collection_response), provenance(items_response)],
        "product": product,
        "warnings": warnings,
    }


class Lc100RedirectGuard(BaseHandler):
    handler_order = 499

    def http_error_302(self, req, fp, code, msg, headers):
        if req.full_url in {COLLECTION_URL, SEARCH_URL}:
            fp.close()
            raise FetchError("LC100 redirect rejected", status=code)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def preflight(fetch=False):
    if not fetch:
        return plan()
    client = Client()
    client.max_bytes = MAX_BYTES
    client.opener.add_handler(Lc100RedirectGuard())
    first = client.get(COLLECTION_URL, headers={"Accept": "application/json"})
    collection_metadata(first)
    second = client.get(SEARCH_URL, headers={"Accept": "application/geo+json,application/json"})
    result = parse_metadata(first, second)
    result["checked_at"] = datetime.now(UTC).isoformat()
    return result
