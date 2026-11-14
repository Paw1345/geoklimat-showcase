"""Disposable EFFIS metadata preflight; no values, assets, approval or SQL."""

import hashlib
import math
import re
from datetime import UTC, datetime
from urllib.request import BaseHandler
from xml.etree import ElementTree as ET

from ingestion.copernicus_wms import MAX_LAYERS, PROFILES, ROOTS, child_text, layer_crs
from ingestion.transport import Client, FetchError

URL = PROFILES["effis"]["url"]
LAYER = "mf010.fwi"
MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 10000
NS = {"w": "http://www.opengis.net/wms", "x": "http://www.w3.org/1999/xlink"}
RESOURCE = "://maps.effis.emergency.copernicus.eu/effis?language=eng&"


def raw(value, limit=160):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(c) < 32 and c not in "\t\r\n" for c in value)
    ):
        raise FetchError("EFFIS metadata text changed")
    return value


def one(node, path):
    for step in path.split("/"):
        matches = node.findall("w:" + step, NS)
        if len(matches) != 1:
            raise FetchError("EFFIS metadata cardinality changed")
        node = matches[0]
    return node


def plan():
    return {
        "request_url": URL,
        "layer_name": LAYER,
        "max_bytes": MAX_BYTES,
        "fetched": False,
        "database_writes": False,
        "resources_downloaded": False,
        "terms_accepted": False,
        "semantic_status": "unconfirmed",
        "product_ready": False,
        "value_unit": None,
        "nodata": None,
        "product_version": None,
        "product_license": None,
        "observation_time": None,
        "model_lead_time": None,
        "full_dataset_coverage": False,
    }


def operations(request):
    result = {}
    for name in ("GetMap", "GetFeatureInfo"):
        operation = one(request, name)
        formats = [raw(n.text, 100).strip() for n in operation.findall("w:Format", NS)]
        if not 1 <= len(formats) <= 32 or len(set(formats)) != len(formats):
            raise FetchError("EFFIS operation formats changed")
        link = one(operation, "DCPType/HTTP/Get/OnlineResource")
        endpoint = raw(link.get("{" + NS["x"] + "}href"), 250)
        if endpoint not in {"http" + RESOURCE, "https" + RESOURCE}:
            raise FetchError("EFFIS advertised operation endpoint changed")
        result[name] = {
            "formats": formats,
            "advertised_endpoint_scheme": endpoint.split(":", 1)[0],
            "endpoint_verified": False,
        }
    return result


def layer_metadata(capability):
    # WMS children inherit CRS, queryable and time; no recursion or child fetch.
    stack = [(one(capability, "Layer"), frozenset(), False, None, None)]
    found = []
    count = 0
    while stack:
        node, crs, queryable, time, bbox = stack.pop()
        count += 1
        if count > MAX_LAYERS:
            raise FetchError("EFFIS layer budget changed")
        if node.get("queryable") is not None:
            flag = node.get("queryable")
            if flag not in {"0", "1"}:
                raise FetchError("EFFIS queryable flag changed")
            queryable = flag == "1"
        if any(n.tag.rsplit("}", 1)[-1] == "CRS" and n.tag != "{" + NS["w"] + "}CRS" for n in node):
            raise FetchError("EFFIS CRS namespace changed")
        own_crs = [raw(n.text, 80).strip().upper() for n in node.findall("w:CRS", NS)]
        if len(own_crs) > 20 or any(not re.fullmatch(r"EPSG:[0-9]{1,6}", c) for c in own_crs):
            raise FetchError("EFFIS CRS declarations changed")
        crs = crs | frozenset(layer_crs(node, "1.3.0"))
        if len(crs) > 20:
            raise FetchError("EFFIS inherited CRS budget changed")
        times = [
            n for n in node.findall("w:Dimension", NS) if (n.get("name") or "").lower() == "time"
        ]
        if len(times) > 1:
            raise FetchError("EFFIS time dimension repeated")
        if times:
            dimension = times[0]
            nearest = dimension.get("nearestValue")
            if nearest not in {None, "0", "1"}:
                raise FetchError("EFFIS time nearest flag changed")
            time = {
                "raw": raw(dimension.text, 2000),
                "units_raw": raw(dimension.get("units"), 80),
                "default_raw": raw(dimension.get("default"), 80),
                "nearest_value_raw": nearest,
                "availability_verified": False,
            }
        boxes = node.findall("w:EX_GeographicBoundingBox", NS)
        if len(boxes) > 1:
            raise FetchError("EFFIS geographic bbox repeated")
        if boxes:
            fields = (
                "westBoundLongitude",
                "eastBoundLongitude",
                "southBoundLatitude",
                "northBoundLatitude",
            )
            bbox = {name: raw(one(boxes[0], name).text, 40) for name in fields}
            try:
                west, east, south, north = [float(bbox[name]) for name in fields]
            except ValueError as exc:
                raise FetchError("EFFIS bbox number changed") from exc
            if not (
                all(math.isfinite(v) for v in (west, east, south, north))
                and -180 <= west < east <= 180
                and -90 <= south < north <= 90
            ):
                raise FetchError("EFFIS geographic bbox changed")
        names = node.findall("w:Name", NS)
        if len(names) > 1:
            raise FetchError("EFFIS layer identity repeated")
        if names and raw(names[0].text).strip() == LAYER:
            if raw(one(node, "Title").text).strip() != LAYER or not crs or not time or not bbox:
                raise FetchError("EFFIS selected layer profile changed")
            styles = node.findall("w:Style", NS)
            if len(styles) > 20:
                raise FetchError("EFFIS style budget changed")
            found.append(
                {
                    "layer": {
                        "name": LAYER,
                        "queryable": queryable,
                        "crs": sorted(crs),
                        "geographic_bbox_raw": bbox,
                        "styles": [raw(one(s, "Name").text) for s in styles],
                    },
                    "time": time,
                }
            )
        stack.extend((n, crs, queryable, time, bbox) for n in node.findall("w:Layer", NS))
    if len(found) != 1:
        raise FetchError("EFFIS selected layer missing or duplicated")
    return found[0]


def parse_metadata(response):
    """Validate reviewed WMS metadata without assuming dates are available values."""
    if (
        response.url != URL
        or response.status != 200
        or not 0 < len(response.body) <= MAX_BYTES
        or response.headers.get("content-type", "").split(";")[0].lower().strip()
        not in {"text/xml", "application/xml"}
    ):
        raise FetchError("EFFIS response identity or budget changed")
    try:
        document = response.body.decode("utf-8")
        if "\x00" in document or "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
            raise FetchError("EFFIS unsafe XML")
        root = ET.fromstring(document)
    except (UnicodeError, ET.ParseError) as exc:
        raise FetchError("EFFIS invalid XML") from exc
    if root.tag != ROOTS["1.3.0"] or root.get("version") != "1.3.0":
        raise FetchError("EFFIS root namespace or version changed")
    if sum(1 for _ in root.iter()) > MAX_NODES:
        raise FetchError("EFFIS node budget changed")
    service = one(root, "Service")
    if not child_text(service, "Title"):
        raise FetchError("EFFIS service identity missing")
    capability = one(root, "Capability")
    selected = layer_metadata(capability)
    operation_info = operations(one(capability, "Request"))
    warnings = [
        "declared_time_not_verified_availability",
        "default_time_not_current_forecast",
        "value_unit_nodata_license_unconfirmed",
        "geographic_bbox_not_poland_mask",
    ]
    if not selected["layer"]["queryable"]:
        warnings.append("layer_not_queryable")
    if any(v["advertised_endpoint_scheme"] == "http" for v in operation_info.values()):
        warnings.append("advertised_operation_http_not_requested")
    return {
        **plan(),
        **selected,
        "fetched": True,
        "status": response.status,
        "access_policy": response.access_policy,
        "bytes": len(response.body),
        "sha256": hashlib.sha256(response.body).hexdigest(),
        "wms_version": "1.3.0",
        "fees_raw": raw(one(service, "Fees").text),
        "access_constraints_raw": raw(one(service, "AccessConstraints").text),
        "operations": operation_info,
        "warnings": warnings,
    }


class FwiRedirectGuard(BaseHandler):
    # Before HTTPDefaultErrorHandler; robots retain standard Client policy.
    handler_order = 499

    def http_error_302(self, req, fp, code, msg, headers):
        if req.full_url == URL:
            fp.close()
            raise FetchError("EFFIS metadata redirect rejected", status=code)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def preflight(fetch=False):
    result = plan()
    if fetch:
        client = Client()
        client.max_bytes = MAX_BYTES
        client.opener.add_handler(FwiRedirectGuard())
        result = parse_metadata(client.get(URL, headers={"Accept": "application/xml,text/xml"}))
        result["checked_at"] = datetime.now(UTC).isoformat()
    return result
