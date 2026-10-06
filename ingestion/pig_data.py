"""Reviewed bounded MIDAS GeoJSON samples, not a complete inventory."""

import json
import math
from urllib.parse import urlencode

from django.conf import settings

from ingestion.transport import FetchError

BASE = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/midas/MapServer/"
TERMS = "https://www.pgi.gov.pl/rejestry-i-bazy-danych-geologicznych/zasady-korzystania-z-isp.html"
DETAIL_TERMS = "https://www.pgi.gov.pl/midas/zrodla-danych.html"
LABELS = {0: "udokumentowane złoża kopalin", 1: "obszary górnicze", 2: "tereny górnicze"}
LIMIT = 100
ATTRIBUTION = (
    "PIG-PIB / MIDAS. Pierwsze100 obiektów według OBJECTID, nie pełny zbiór ani losowa próba. "
    "Geometrie przeliczone przez dostawcę do WGS84; atrybuty bez interpretacji dat. "
    "Retencja30dni dotyczy kopii, nie wieku danych. Czas wytworzenia niepotwierdzony; "
    "czas pozyskania w eksporcie. Warunki i ograniczenia dokładności: "
    + TERMS
    + " oraz "
    + DETAIL_TERMS
)


def channel_url(layer):
    return BASE + str(layer) + "/query"


def config(layer):
    return {"mode": "pig_midas_sample", "layer": layer, "feature_limit": LIMIT}


def slug(layer):
    return "pig-midas-" + str(layer) + "-sample"


def read(client, url):
    response = client.get(url)
    if response.status != 200 or response.url != url:
        raise FetchError("MIDAS response identity changed")
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError("MIDAS invalid JSON") from exc
    if not isinstance(data, dict) or "error" in data:
        raise FetchError("MIDAS error or schema changed")
    return data


def validate_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise FetchError("MIDAS polygon geometry missing")
    coordinates = geometry.get("coordinates")
    polygons = [coordinates] if geometry["type"] == "Polygon" else coordinates
    positions = 0
    if not isinstance(polygons, list) or not polygons:
        raise FetchError("MIDAS coordinates missing")
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise FetchError("MIDAS rings missing")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise FetchError("MIDAS ring not closed")
            for point in ring:
                positions += 1
                if (
                    positions > 100000
                    or not isinstance(point, list)
                    or len(point) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
                    or not -180 <= point[0] <= 180
                    or not -90 <= point[1] <= 90
                ):
                    raise FetchError("MIDAS coordinate outside WGS84 or budget")


def collect_sample(channel, client):
    layer = channel.config.get("layer")
    if (
        type(layer) is not int
        or layer not in LABELS
        or channel.kind != "geojson"
        or channel.region != "PL"
        or channel.url != channel_url(layer)
        or channel.config != config(layer)
    ):
        raise FetchError("MIDAS channel outside reviewed sample profile")
    count_url = (
        channel.url + "?" + urlencode({"f": "json", "where": "1=1", "returnCountOnly": "true"})
    )
    count = read(client, count_url).get("count")
    if type(count) is not int or not 0 <= count <= 1000000:
        raise FetchError("MIDAS count missing or outside budget")
    request_url = (
        channel.url
        + "?"
        + urlencode(
            {
                "f": "geojson",
                "where": "1=1",
                "outFields": "*",
                "outSR": "4326",
                "returnGeometry": "true",
                "resultRecordCount": LIMIT,
                "resultOffset": 0,
                "orderByFields": "OBJECTID ASC",
            }
        )
    )
    data = read(client, request_url)
    features = data.get("features")
    if data.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise FetchError("MIDAS GeoJSON features missing")
    if len(features) > LIMIT or len(features) > count or (count and not features):
        raise FetchError("MIDAS sample size inconsistent")
    previous = -1
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise FetchError("MIDAS feature schema changed")
        identity, properties = feature.get("id"), feature.get("properties")
        if (
            type(identity) is not int
            or identity <= previous
            or not isinstance(properties, dict)
            or properties.get("OBJECTID") != identity
            or type(properties.get("OBJECTID")) is not int
        ):
            raise FetchError("MIDAS identity/order changed")
        validate_geometry(feature.get("geometry"))
        previous = identity
    exceeded = data.get("exceededTransferLimit", False)
    if type(exceeded) is not bool:
        raise FetchError("MIDAS transfer flag changed")
    after_count = read(client, count_url).get("count")
    if type(after_count) is not int or after_count != count:
        raise FetchError("MIDAS count changed during sample; retry")
    partial = exceeded or count > len(features)
    data["geoklimat_provenance"] = {
        "publisher": "PIG-PIB",
        "dataset": "MIDAS",
        "layer": layer,
        "source_url": request_url,
        "terms_urls": [TERMS, DETAIL_TERMS],
        "attribution": ATTRIBUTION,
        "source_generated_at": None,
        "transformation": "first100 OBJECTID ASC; supplier projection to EPSG:4326",
        "source_count": count,
        "partial": partial,
    }
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("MIDAS automatic payload exceeds budget")
    return (
        payload,
        "application/json",
        {
            "kind": "geojson",
            "scope": "PL",
            "collector": "pig_midas_sample",
            "records": len(features),
            "source_count": count,
            "partial": partial,
            "request_url": request_url,
            "sample_limit": LIMIT,
            "crs": "EPSG:4326",
            "retention_basis": "local_copy_age",
        },
    )
