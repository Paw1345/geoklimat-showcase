"""Reviewed boundary sources: PRG (GUGiK) state boundary over the public WFS (PL)
and Natural Earth admin-0 countries for the EU27 mask (EU).

The provider reprojects EPSG:2180 to EPSG:4326 on request. Locally the GML is only
parsed: no reprojection, simplification or repair. Axis order is detected from
Poland's coordinate ranges, which do not overlap (lon 14-24.2, lat 49-55).
"""

import json
import re
from urllib.parse import urlencode
from xml.etree import ElementTree

from ingestion.boundary_mask import MaskError, to_shape
from ingestion.transport import FetchError

PRG_WFS = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries"
PRG_TYPE = "ms:A00_Granice_panstwa"
PRG_MAX_BYTES = 50 * 1024 * 1024
# Generous PL envelope incl. territorial sea; anything outside means a wrong layer/axis.
PL_ENVELOPE = (13.5, 48.5, 25.0, 55.5)
PRG = {
    "publisher": "Główny Urząd Geodezji i Kartografii (GUGiK)",
    "source": "Państwowy Rejestr Granic (PRG), WFS AdministrativeBoundaries",
    "source_url": PRG_WFS,
    "source_crs": "EPSG:2180 (reprojected to EPSG:4326 by the provider WFS)",
    "licence": "PZGiK/PRG: bezpłatnie, do dowolnego użytku (geoportal.gov.pl)",
    "attribution": "Źródło: Państwowy Rejestr Granic, GUGiK",
}


def capabilities_url():
    return PRG_WFS + "?" + urlencode({"SERVICE": "WFS", "REQUEST": "GetCapabilities"})


def feature_url(type_name=PRG_TYPE):
    return (
        PRG_WFS
        + "?"
        + urlencode(
            {
                "SERVICE": "WFS",
                "VERSION": "2.0.0",
                "REQUEST": "GetFeature",
                "TYPENAMES": type_name,
                "SRSNAME": "urn:ogc:def:crs:EPSG::4326",
            }
        )
    )


def _xml(body):
    if b"\x00" in body or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", body, re.I):
        raise FetchError("PRG XML with DTD/entity refused")
    try:
        return ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise FetchError("PRG XML invalid") from exc


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def check_capabilities(body, type_name=PRG_TYPE):
    """The layer must exist and advertise EPSG:4326; otherwise nothing is fetched."""
    root = _xml(body)
    for element in root.iter():
        if _local(element.tag) != "FeatureType":
            continue
        names = [c.text for c in element if _local(c.tag) == "Name"]
        if type_name not in names:
            continue
        crs = [
            (c.text or "").strip()
            for c in element
            if _local(c.tag) in {"DefaultCRS", "OtherCRS", "DefaultSRS", "OtherSRS"}
        ]
        if not any(value.endswith(("EPSG::4326", "EPSG:4326")) for value in crs):
            raise FetchError("PRG layer does not offer EPSG:4326")
        return {"type_name": type_name, "crs": crs}
    raise FetchError("PRG layer not advertised in capabilities")


def _ring(element):
    lists = [e for e in element.iter() if _local(e.tag) == "posList"]
    if len(lists) != 1:
        raise FetchError("PRG ring must have exactly one posList")
    dimension = lists[0].get("srsDimension") or lists[0].get("dimension") or "2"
    if dimension != "2":
        raise FetchError("PRG ring must be 2D")
    try:
        values = [float(v) for v in (lists[0].text or "").split()]
    except ValueError as exc:
        raise FetchError("PRG posList not numeric") from exc
    if not values or len(values) % 2:
        raise FetchError("PRG posList length invalid")
    return [[values[i], values[i + 1]] for i in range(0, len(values), 2)]


def _polygons(member):
    polygons = []
    for element in member.iter():
        if _local(element.tag) != "Polygon":
            continue
        rings = []
        for child in element:
            if _local(child.tag) in {"exterior", "interior"}:
                rings.append((_local(child.tag), _ring(child)))
        if not rings or rings[0][0] != "exterior" or [r[0] for r in rings].count("exterior") > 1:
            raise FetchError("PRG polygon must start with one exterior ring")
        polygons.append([ring for _, ring in rings])
    return polygons


def _orient(polygons):
    """Return lon/lat order; refuse anything that is not plainly inside PL."""
    points = [p for polygon in polygons for ring in polygon for p in ring]
    west, south, east, north = PL_ENVELOPE

    def inside(order):
        return all(west <= p[order[0]] <= east and south <= p[order[1]] <= north for p in points)

    if inside((0, 1)):
        return polygons, "lon_lat"
    if inside((1, 0)):
        return [[[[p[1], p[0]] for p in ring] for ring in polygon] for polygon in polygons], (
            "lat_lon_swapped"
        )
    raise FetchError("PRG coordinates outside the Polish envelope in both axis orders")


def parse_features(body):
    root = _xml(body)
    if _local(root.tag) == "ExceptionReport":
        raise FetchError("PRG WFS exception report")
    members = [e for e in root if _local(e.tag) in {"member", "featureMember", "featureMembers"}]
    features = []
    for member in members:
        polygons = _polygons(member)
        if not polygons:
            raise FetchError("PRG member without polygon geometry")
        polygons, axis = _orient(polygons)
        geometry = (
            {"type": "Polygon", "coordinates": polygons[0]}
            if len(polygons) == 1
            else {"type": "MultiPolygon", "coordinates": polygons}
        )
        try:
            to_shape(geometry, budget=5_000_000)
        except MaskError as exc:
            raise FetchError(f"PRG geometry refused: {exc.reason}") from exc
        features.append(
            {
                "type": "Feature",
                "properties": {"axis_order_received": axis},
                "geometry": geometry,
            }
        )
    if not features:
        raise FetchError("PRG returned no features")
    return {"type": "FeatureCollection", "features": features}


def fetch_prg(client, type_name=PRG_TYPE):
    caps = check_capabilities(client.get(capabilities_url()).body, type_name)
    old = client.max_bytes
    client.max_bytes = PRG_MAX_BYTES
    try:
        response = client.get(feature_url(type_name))
    finally:
        client.max_bytes = old
    return parse_features(response.body), caps


# Natural Earth admin-0 countries, release v5.1.2 pinned by tag: public domain, no account,
# no licence acceptance. WGS84 lon/lat (CRS84) as published; 1:10 m generalisation, so the
# EU mask is coarse at borders (PL uses PRG). One feature per EU27 member by ADM0_A3.
NE_VERSION = "v5.1.2"
NE_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
    + NE_VERSION
    + "/geojson/ne_10m_admin_0_countries.geojson"
)
NE_MAX_BYTES = 50 * 1024 * 1024
NE_CRS = {"urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:EPSG::4326"}
EU27 = (
    "AUT BEL BGR HRV CYP CZE DNK EST FIN FRA DEU GRC HUN IRL ITA "
    "LVA LTU LUX MLT NLD POL PRT ROU SVK SVN ESP SWE"
).split()
# EU territory that Natural Earth keeps as separate admin-0 units: Åland (FI) and the
# French part of Saint-Martin (outermost region). Northern Cyprus (CYN) stays out:
# the acquis is suspended there.
EU_PARTS = ["ALD", "MAF"]
# Natural Earth's NLD includes Caribbean Netherlands (overseas countries and territories,
# not EU territory). Only those parts west of this meridian are left out; no other change.
NLD_OCT_WEST_OF = -30.0
NATURAL_EARTH = {
    "publisher": "Natural Earth (NACIS)",
    "source": "Natural Earth 1:10m Admin 0 – Countries, EU27 members + Åland, Saint-Martin",
    "source_url": NE_URL,
    "source_crs": "WGS84 lon/lat (CRS84) as published; no reprojection",
    "version": "naturalearth " + NE_VERSION,
    "licence": "Public domain (naturalearthdata.com/about/terms-of-use)",
    "attribution": "Made with Natural Earth (optional; public domain)",
}


def _outside_eu(code, polygon):
    return code == "NLD" and all(p[0] < NLD_OCT_WEST_OF for p in polygon[0])


def parse_naturalearth(body):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError("Natural Earth GeoJSON invalid") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise FetchError("Natural Earth FeatureCollection missing")
    crs = ((data.get("crs") or {}).get("properties") or {}).get("name")
    if crs not in NE_CRS:
        raise FetchError("Natural Earth CRS is not WGS84 lon/lat")
    found = {}
    for feature in data.get("features") or []:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        code = properties.get("ADM0_A3") if isinstance(properties, dict) else None
        if code not in EU27 + EU_PARTS:
            continue
        if code in found:
            raise FetchError(f"Natural Earth repeats {code}")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") not in {
            "Polygon",
            "MultiPolygon",
        }:
            raise FetchError(f"Natural Earth {code} is not polygonal")
        polygons = (
            [geometry["coordinates"]]
            if geometry["type"] == "Polygon"
            else list(geometry["coordinates"])
        )
        kept = [p for p in polygons if not _outside_eu(code, p)]
        geometry = (
            {"type": "Polygon", "coordinates": kept[0]}
            if len(kept) == 1
            else {"type": "MultiPolygon", "coordinates": kept}
        )
        try:
            to_shape(geometry, budget=5_000_000)
        except MaskError as exc:
            raise FetchError(f"Natural Earth {code} refused: {exc.reason}") from exc
        found[code] = {
            "type": "Feature",
            "properties": {
                "ADM0_A3": code,
                "NAME": properties.get("NAME"),
                "parts_left_out": len(polygons) - len(kept),
            },
            "geometry": geometry,
        }
    missing = [code for code in EU27 + EU_PARTS if code not in found]
    if missing:
        raise FetchError("Natural Earth lacks EU members: " + ", ".join(missing))
    return {"type": "FeatureCollection", "features": [found[code] for code in EU27 + EU_PARTS]}


def fetch_naturalearth(client):
    old = client.max_bytes
    client.max_bytes = NE_MAX_BYTES
    try:
        response = client.get(NE_URL)
    finally:
        client.max_bytes = old
    return parse_naturalearth(response.body)
