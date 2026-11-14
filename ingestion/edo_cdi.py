"""Disposable CDI DescribeCoverage preflight, not a raster collector or approval."""

import hashlib
import math
import re
from datetime import UTC, datetime
from urllib.request import BaseHandler
from xml.etree import ElementTree as ET

from ingestion.transport import Client, FetchError

URL = (
    "https://drought.emergency.copernicus.eu/api/wcs?map=do_wcs&SERVICE=WCS&VERSION=2.0.0"
    "&REQUEST=DescribeCoverage&coverageID=cdiad"
)
MAX_BYTES = 2 * 1024 * 1024
CRS = "http://www.opengis.net/def/crs/EPSG/0/4326"
NS = {
    "wcs": "http://www.opengis.net/wcs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "gmlcov": "http://www.opengis.net/gmlcov/1.0",
    "swe": "http://www.opengis.net/swe/2.0",
}


def one(node, path):
    for step in path.split("/"):
        matches = node.findall(step, NS)
        if len(matches) != 1:
            raise FetchError("EDO CDI metadata cardinality changed")
        node = matches[0]
    return node


def raw(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise FetchError("EDO CDI metadata text changed")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        raise FetchError("EDO CDI metadata control character")
    return value


def vector(value, integer=False):
    parts = raw(value).split()
    if len(parts) != 2 or any(len(part) > 40 for part in parts):
        raise FetchError("EDO CDI axis count changed")
    pattern = (
        r"[0-9]{1,10}" if integer else r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
    )
    if any(not re.fullmatch(pattern, part) for part in parts):
        raise FetchError("EDO CDI numeric metadata changed")
    values = [int(part) if integer else float(part) for part in parts]
    if not all(math.isfinite(value) for value in values):
        raise FetchError("EDO CDI non-finite metadata")
    return values


def plan():
    return {
        "request_url": URL,
        "max_bytes": MAX_BYTES,
        "fetched": False,
        "database_writes": False,
        "resources_downloaded": False,
        "semantic_status": "unconfirmed",
        "product_version": None,
        "observation_time": None,
        "nodata": None,
        "full_dataset_coverage": False,
        "terms_accepted": False,
    }


def parse_description(response):
    """Validate the reviewed metadata shape; retain raw numbers and axis order."""
    if (
        response.url != URL
        or response.status != 200
        or not 0 < len(response.body) <= MAX_BYTES
        or response.headers.get("content-type", "").split(";")[0].lower().strip()
        not in {"text/xml", "application/xml"}
    ):
        raise FetchError("EDO CDI response identity or budget changed")
    try:
        document = response.body.decode("utf-8")
        if "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper() or "\x00" in document:
            raise FetchError("EDO CDI unsafe XML")
        root = ET.fromstring(document)
    except (UnicodeError, ET.ParseError) as exc:
        raise FetchError("EDO CDI invalid XML") from exc
    if (
        root.tag != "{" + NS["wcs"] + "}CoverageDescriptions"
        or len(root) != 1
        or len(list(root.iter())) > 100
    ):
        raise FetchError("EDO CDI root or node budget changed")
    description = one(root, "wcs:CoverageDescription")
    if (
        description.get("{" + NS["gml"] + "}id") != "cdiad"
        or raw(one(description, "wcs:CoverageId").text).strip() != "cdiad"
    ):
        raise FetchError("EDO CDI coverage identity changed")
    envelope = one(description, "gml:boundedBy/gml:Envelope")
    lower = one(envelope, "gml:lowerCorner").text
    upper = one(envelope, "gml:upperCorner").text
    low, high = vector(lower), vector(upper)
    if (
        envelope.get("srsName") != CRS
        or envelope.get("srsDimension") != "2"
        or raw(envelope.get("axisLabels")).split() != ["lat", "long"]
        or raw(envelope.get("uomLabels")).split() != ["deg", "deg"]
        or not (-90 <= low[0] < high[0] <= 90 and -180 <= low[1] < high[1] <= 180)
    ):
        raise FetchError("EDO CDI envelope axes or extent changed")
    grid = one(description, "gml:domainSet/gml:RectifiedGrid")
    grid_low = one(grid, "gml:limits/gml:GridEnvelope/gml:low").text
    grid_high = one(grid, "gml:limits/gml:GridEnvelope/gml:high").text
    a, b = vector(grid_low, True), vector(grid_high, True)
    labels = one(grid, "gml:axisLabels").text
    point = one(grid, "gml:origin/gml:Point")
    origin = one(point, "gml:pos").text
    pos = vector(origin)
    offsets = grid.findall("gml:offsetVector", NS)
    if (
        grid.get("dimension") != "2"
        or raw(labels).split() != ["long", "lat"]
        or any(x > y for x, y in zip(a, b, strict=True))
        or point.get("srsName") != CRS
        or any(not x <= z <= y for x, z, y in zip(low, pos, high, strict=True))
        or len(offsets) != 2
        or any(offset.get("srsName") != CRS for offset in offsets)
    ):
        raise FetchError("EDO CDI grid axes or origin changed")
    vectors = [vector(offset.text) for offset in offsets]
    if any(abs(v) > 360 for row in vectors for v in row) or (
        vectors[0][0] * vectors[1][1] - vectors[0][1] * vectors[1][0] == 0
    ):
        raise FetchError("EDO CDI degenerate grid")
    field = one(description, "gmlcov:rangeType/swe:DataRecord/swe:field")
    quantity = one(field, "swe:Quantity")
    nil_values = one(quantity, "swe:nilValues")
    unit = raw(one(quantity, "swe:uom").get("code"))
    interval = one(quantity, "swe:constraint/swe:AllowedValues/swe:interval").text
    allowed = vector(interval)
    figures = raw(one(quantity, "swe:constraint/swe:AllowedValues/swe:significantFigures").text)
    subtype = raw(one(description, "wcs:ServiceParameters/wcs:CoverageSubtype").text).strip()
    native = one(description, "wcs:ServiceParameters/wcs:nativeFormat")
    if (
        field.get("name") != "band1"
        or allowed[0] > allowed[1]
        or len(nil_values) != 0
        or (nil_values.text or "").strip()
        or not re.fullmatch(r"[0-9]{1,2}", figures.strip())
        or subtype != "RectifiedGridCoverage"
        or len(native) != 0
        or (native.text or "").strip()
    ):
        raise FetchError("EDO CDI range metadata changed")
    warnings = ["categorical_not_physical_measurement", "version_time_nodata_unconfirmed"]
    if unit == "W.m-2.Sr-1":
        warnings.append("unit_mismatch")
    else:
        warnings.append("unit_semantics_unconfirmed")
    return {
        **plan(),
        "fetched": True,
        "status": response.status,
        "access_policy": response.access_policy,
        "bytes": len(response.body),
        "sha256": hashlib.sha256(response.body).hexdigest(),
        "coverage_id": "cdiad",
        "envelope": {
            "crs_raw": CRS,
            "axis_labels_raw": envelope.get("axisLabels"),
            "axis_units_raw": envelope.get("uomLabels"),
            "lower_raw": lower,
            "upper_raw": upper,
        },
        "grid": {
            "axis_labels_raw": labels,
            "low_raw": grid_low,
            "high_raw": grid_high,
            "origin_raw": origin,
            "offset_vectors_raw": [offset.text for offset in offsets],
        },
        "range": {
            "field_raw": "band1",
            "uom_raw": unit,
            "allowed_interval_raw": interval,
            "significant_figures_raw": figures,
        },
        "warnings": warnings,
    }


class CdiRedirectGuard(BaseHandler):
    # Runs before Client's NoRedirect/HTTPDefaultErrorHandler; robots keep normal policy.
    handler_order = 499

    def http_error_302(self, req, fp, code, msg, headers):
        if req.full_url == URL:
            fp.close()
            raise FetchError("EDO CDI redirect rejected", status=code)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def preflight(fetch=False):
    """Default is a no-I/O plan; fetch reads only this reviewed metadata endpoint."""
    result = plan()
    if fetch:
        client = Client()
        client.max_bytes = MAX_BYTES
        client.opener.add_handler(CdiRedirectGuard())
        result = parse_description(client.get(URL, headers={"Accept": "application/xml,text/xml"}))
        result["checked_at"] = datetime.now(UTC).isoformat()
    return result
