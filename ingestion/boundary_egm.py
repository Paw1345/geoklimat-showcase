"""EU mask from a locally downloaded EuroGlobalMap GeoPackage (EuroGeographics).

The operator downloads the package after reviewing its terms; this module only
reads it (sqlite3, read-only URI). EU27 member polygons are selected by country
code; the administrative units of each member (or shared ``A#B`` area) are
joined with ``union_all``, which drops internal borders of valid parts and is
not a repair. Coordinates stay as delivered in ETRS89 (EPSG:4258), stored as
EPSG:4326: the frames differ by under 1 m, far below the 1:1 000 000 product
accuracy. No reprojection, simplification or repair.
"""

import sqlite3
import struct
from contextlib import closing
from pathlib import Path

from ingestion.boundary_mask import MaskError, to_shape
from ingestion.transport import FetchError

# Greece is "EL" in EU/Eurostat usage and "GR" in ISO 3166; both are accepted.
EU27 = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}  # fmt: skip
EU27_COUNT = 27
ACCEPTED_SRS = {4258, 4326}
MAX_FEATURES = 250_000
MAX_PART_POSITIONS = 5_000_000
DEFAULT_LAYER = "PolbndA"
DEFAULT_FIELD = "ICC"
EGM = {
    "publisher": "EuroGeographics (Open Maps for Europe)",
    "source": "EuroGlobalMap, GeoPackage",
    "source_url": "https://www.mapsforeurope.org/datasets/euro-global-map",
    "source_crs": "EPSG:4258 (ETRS89), stored unchanged as EPSG:4326 (<1 m)",
    "attribution": "© EuroGeographics",
}
_ENVELOPE = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def _identifier(value):
    if not value.replace("_", "").isalnum():
        raise FetchError("GeoPackage identifier refused")
    return '"' + value + '"'


def _wkb(blob):
    """GeoPackage binary header -> standard WKB (no extended geometries)."""
    blob = bytes(blob)
    if len(blob) < 8 or blob[:2] != b"GP" or blob[2] != 0:
        raise FetchError("GeoPackage geometry header invalid")
    flags = blob[3]
    if flags & 0b100000 or flags & 0b10000:
        raise FetchError("GeoPackage extended or empty geometry refused")
    envelope = _ENVELOPE.get((flags >> 1) & 0b111)
    if envelope is None:
        raise FetchError("GeoPackage envelope code invalid")
    order = "<" if flags & 1 else ">"
    srs_id = struct.unpack(order + "i", blob[4:8])[0]
    return srs_id, blob[8 + envelope :]


def inspect(path):
    """Layers with geometry column, SRS and fields; used by dry-run to explain refusals."""
    with closing(_connect(path)) as db:
        layers = db.execute(
            "SELECT table_name, column_name, srs_id FROM gpkg_geometry_columns ORDER BY 1"
        ).fetchall()
        result = []
        for table, column, srs in layers:
            fields = [row[1] for row in db.execute(f"PRAGMA table_info({_identifier(table)})")]
            result.append({"layer": table, "geometry": column, "srs_id": srs, "fields": fields})
    return result


def _connect(path):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".gpkg":
        raise FetchError("EuroGlobalMap GeoPackage file not found")
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def read_eu(path, layer=DEFAULT_LAYER, field=DEFAULT_FIELD):
    from shapely import union_all
    from shapely.errors import GEOSException
    from shapely.geometry import mapping

    layers = {item["layer"]: item for item in inspect(path)}
    if layer not in layers or field not in layers[layer]["fields"]:
        raise FetchError(f"Layer {layer} with field {field} not in GeoPackage")
    meta = layers[layer]
    if meta["srs_id"] not in ACCEPTED_SRS:
        raise FetchError("GeoPackage layer CRS is not ETRS89/WGS84 geographic")
    sql = (
        f"SELECT {_identifier(field)}, {_identifier(meta['geometry'])} "
        f"FROM {_identifier(layer)} LIMIT {MAX_FEATURES + 1}"
    )
    groups, seen = {}, 0
    with closing(_connect(path)) as db:
        for code, blob in db.execute(sql):
            seen += 1
            if seen > MAX_FEATURES:
                raise FetchError("GeoPackage layer exceeds feature budget")
            _collect(groups, code, blob, meta["srs_id"])
    countries = sorted({c for key in groups for c in key.split("#")})
    if len(countries) != EU27_COUNT:
        missing = sorted({_member(c) for c in EU27} - set(countries))
        raise FetchError("EU27 incomplete in GeoPackage; missing: " + ", ".join(missing))
    features, interiors = [], 0
    for key, parts in sorted(groups.items()):
        try:
            joined = union_all(parts)
            valid = joined.geom_type in {"Polygon", "MultiPolygon"} and joined.is_valid
        except (GEOSException, ValueError, TypeError) as exc:
            raise FetchError("EuroGlobalMap union failed") from exc
        if not valid:
            raise FetchError(f"EuroGlobalMap union of {key} invalid; no automatic repair")
        polygons = getattr(joined, "geoms", [joined])
        interiors += sum(len(polygon.interiors) for polygon in polygons)
        features.append(
            {
                "type": "Feature",
                "properties": {field: key, "units": len(parts)},
                "geometry": _lists(mapping(joined)),
            }
        )
    details = {
        "countries": countries,
        "units": sum(len(parts) for parts in groups.values()),
        "features": len(features),
        "interior_rings": interiors,
    }
    return {"type": "FeatureCollection", "features": features}, details


def _collect(groups, code, blob, layer_srs):
    from shapely import from_wkb
    from shapely.geometry import mapping

    codes = {_member(c) for c in str(code or "").split("#") if c.strip()}
    if not codes or not codes <= EU27:
        # Non-EU parts and areas shared with a non-member stay outside the mask.
        return
    srs_id, wkb = _wkb(blob)
    if srs_id != layer_srs:
        raise FetchError("GeoPackage geometry SRS differs from its layer")
    value = from_wkb(wkb)
    if value.has_z or value.geom_type not in {"Polygon", "MultiPolygon"}:
        raise FetchError("EuroGlobalMap part must be a 2D polygon")
    try:
        to_shape(_lists(mapping(value)), budget=MAX_PART_POSITIONS)
    except MaskError as exc:
        raise FetchError(f"EuroGlobalMap geometry refused: {exc.reason}") from exc
    groups.setdefault("#".join(sorted(codes)), []).append(value)


def _member(code):
    code = code.strip()
    return "EL" if code == "GR" else code


def _lists(value):
    if isinstance(value, (list, tuple)):
        return [_lists(v) for v in value]
    if isinstance(value, dict):
        return {k: _lists(v) for k, v in value.items()}
    return value
