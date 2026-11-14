"""Versioned PL/EU administrative masks shared by collectors and exports.

The mask is read from a pinned local file listed in data/boundaries/manifest.json.
No HTTP, reprojection, simplification or repair: an invalid source refuses.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from ingestion.transport import FetchError

REGIONS = {"PL", "EU"}
STORED_CRS = "EPSG:4326"
MAX_MASK_BYTES = 64 * 1024 * 1024
MAX_MASK_POSITIONS = 5_000_000
MAX_RECORD_POSITIONS = 100_000
RELATIONS = ("inside", "boundary", "outside")
REQUIRED = (
    "region",
    "file",
    "sha256",
    "crs",
    "source_crs",
    "version",
    "publisher",
    "source",
    "source_url",
    "licence",
    "attribution",
    "retrieved_at",
)
_CACHE = {}


class MaskError(FetchError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def manifest_path():
    return Path(settings.BASE_DIR) / "data" / "boundaries" / "manifest.json"


def read_manifest(path=None):
    path = Path(path or manifest_path())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MaskError("missing", "Boundary mask manifest not installed") from exc
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise MaskError("manifest", "Boundary mask manifest unreadable") from exc
    masks = data.get("masks") if isinstance(data, dict) else None
    if not isinstance(masks, list):
        raise MaskError("manifest", "Boundary mask manifest schema invalid")
    entries = {}
    for entry in masks:
        if (
            not isinstance(entry, dict)
            or any(not isinstance(entry.get(key), str) or not entry[key] for key in REQUIRED)
            or entry["region"] not in REGIONS
            or entry["region"] in entries
            or entry["crs"] != STORED_CRS
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            or not re.fullmatch(r"[A-Za-z0-9._-]+\.geojson", entry["file"])
        ):
            raise MaskError("manifest", "Boundary mask manifest entry invalid")
        entries[entry["region"]] = entry
    return path, entries


def _valid_point(point):
    return (
        isinstance(point, list)
        and len(point) == 2
        and all(type(v) in (int, float) and math.isfinite(v) for v in point)
        and -180 <= point[0] <= 180
        and -90 <= point[1] <= 90
    )


def _positions(geometry, budget):
    kind = geometry.get("type") if isinstance(geometry, dict) else None
    coordinates = geometry.get("coordinates") if kind else None
    if kind == "Point":
        rings = [[coordinates]]
    elif kind == "MultiPoint":
        rings = [coordinates] if isinstance(coordinates, list) and coordinates else None
    elif kind in {"Polygon", "MultiPolygon"}:
        polygons = [coordinates] if kind == "Polygon" else coordinates
        if not isinstance(polygons, list) or not polygons:
            raise MaskError("geometry", "Polygon coordinates missing")
        rings = []
        for polygon in polygons:
            if not isinstance(polygon, list) or not polygon:
                raise MaskError("geometry", "Polygon rings missing")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise MaskError("geometry", "Polygon ring not closed")
                rings.append(ring)
    else:
        raise MaskError("geometry", "Unsupported geometry type")
    if rings is None:
        raise MaskError("geometry", "Geometry coordinates missing")
    count = 0
    for ring in rings:
        count += len(ring)
        if count > budget:
            raise MaskError("budget", "Geometry position budget exceeded")
        if not all(_valid_point(point) for point in ring):
            raise MaskError("geometry", "Coordinate not 2D WGS84")
        if kind in {"Polygon", "MultiPolygon"} and any(
            abs(a[0] - b[0]) > 180 for a, b in zip(ring[:-1], ring[1:], strict=True)
        ):
            raise MaskError("antimeridian", "Antimeridian edge needs a reviewed scope")
    return count


def to_shape(geometry, budget=MAX_RECORD_POSITIONS):
    """Validated Shapely geometry; invalid topology is refused, never repaired."""
    from shapely.errors import GEOSException
    from shapely.geometry import shape

    _positions(geometry, budget)
    try:
        value = shape(geometry)
        valid = not value.is_empty and value.is_valid
    except (GEOSException, ValueError, TypeError) as exc:
        raise MaskError("operation", "Geometry operation failed") from exc
    if not valid:
        raise MaskError("topology", "Geometry topology invalid; no automatic repair")
    return value


@dataclass(frozen=True)
class AreaMask:
    region: str
    version: str
    publisher: str
    source: str
    source_url: str
    source_crs: str
    licence: str
    attribution: str
    retrieved_at: str
    sha256: str
    positions: int
    parts: int
    geometry: object

    def relation(self, geometry):
        """inside / boundary / outside of the closed mask; holes are outside."""
        from shapely.errors import GEOSException

        value = to_shape(geometry)
        try:
            if self.geometry.contains_properly(value):
                return "inside"
            if self.geometry.intersects(value):
                return "boundary"
        except (GEOSException, ValueError, TypeError) as exc:
            raise MaskError("operation", "Mask operation failed") from exc
        return "outside"

    def includes(self, geometry):
        """Shared collector/export rule: a record touching the closed mask is in scope."""
        return self.relation(geometry) != "outside"

    def bbox_relation(self, bbox):
        """Catalogue bbox stays a bbox; this only says how it relates to the mask."""
        west, south, east, north = bbox
        ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
        return self.relation({"type": "Polygon", "coordinates": [ring]})

    def provenance(self):
        return {
            "region": self.region,
            "version": self.version,
            "publisher": self.publisher,
            "source": self.source,
            "source_url": self.source_url,
            "source_crs": self.source_crs,
            "crs": STORED_CRS,
            "licence": self.licence,
            "attribution": self.attribution,
            "retrieved_at": self.retrieved_at,
            "sha256": self.sha256,
            "positions": self.positions,
            "parts": self.parts,
            "rule": "closed_mask_intersection",
            "geodesic": False,
            "repaired": False,
            "simplified": False,
        }


def _polygons(document):
    if not isinstance(document, dict):
        raise MaskError("geometry", "Mask GeoJSON invalid")
    if document.get("type") == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list) or not features:
            raise MaskError("geometry", "Mask has no features")
        geometries = [f.get("geometry") if isinstance(f, dict) else None for f in features]
    else:
        geometries = [document]
    for geometry in geometries:
        if not isinstance(geometry, dict) or geometry.get("type") not in {
            "Polygon",
            "MultiPolygon",
        }:
            raise MaskError("geometry", "Mask parts must be polygons")
    return geometries


def load_mask(region, path=None):
    """Load one pinned mask; SHA-256, CRS and topology are checked before use."""
    from shapely import union_all
    from shapely.errors import GEOSException

    path, entries = read_manifest(path)
    entry = entries.get(region)
    if entry is None:
        raise MaskError("missing", f"Boundary mask {region} not installed")
    file = path.parent / entry["file"]
    key = (str(file), entry["sha256"])
    if key in _CACHE:
        return _CACHE[key]
    try:
        if file.stat().st_size > MAX_MASK_BYTES:
            raise MaskError("budget", "Mask file exceeds size budget")
        raw = file.read_bytes()
    except FileNotFoundError as exc:
        raise MaskError("missing", f"Boundary mask {region} file missing") from exc
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise MaskError("checksum", f"Boundary mask {region} checksum mismatch")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MaskError("geometry", "Mask GeoJSON invalid") from exc
    if isinstance(document, dict) and document.get("crs") not in (None, {}):
        raise MaskError("crs", "Mask must be plain GeoJSON in EPSG:4326")
    parts, positions = [], 0
    for geometry in _polygons(document):
        positions += _positions(geometry, MAX_MASK_POSITIONS - positions)
        parts.append(to_shape(geometry, MAX_MASK_POSITIONS))
    try:
        # Union of valid national parts dissolves internal borders; it is not a repair.
        union = union_all(parts)
        valid = union.geom_type in {"Polygon", "MultiPolygon"} and union.is_valid
    except (GEOSException, ValueError, TypeError) as exc:
        raise MaskError("operation", "Mask union failed") from exc
    if not valid or union.is_empty:
        raise MaskError("topology", "Mask union invalid; no automatic repair")
    from shapely import prepare

    prepare(union)
    mask = AreaMask(
        region=region,
        version=entry["version"],
        publisher=entry["publisher"],
        source=entry["source"],
        source_url=entry["source_url"],
        source_crs=entry["source_crs"],
        licence=entry["licence"],
        attribution=entry["attribution"],
        retrieved_at=entry["retrieved_at"],
        sha256=entry["sha256"],
        positions=positions,
        parts=len(parts),
        geometry=union,
    )
    _CACHE[key] = mask
    return mask


def install(region, document, meta, path=None, apply=False, now=None):
    """Pin one mask file and manifest entry; dry-run validates in a temporary copy."""
    import tempfile

    from django.utils import timezone

    path = Path(path or manifest_path())
    now = now or timezone.now()
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    entry = {
        **{key: meta[key] for key in REQUIRED if key in meta},
        "region": region,
        "file": f"{region.lower()}-{meta['slug']}-{now:%Y%m%d}-{digest[:12]}.geojson",
        "sha256": digest,
        "crs": STORED_CRS,
        "retrieved_at": now.isoformat(),
    }
    entry.setdefault("version", f"{meta['slug']} {now:%Y-%m-%d}")
    try:
        _, entries = read_manifest(path)
    except MaskError as exc:
        if exc.reason != "missing":
            raise
        entries = {}
    current = entries.get(region)
    if current and current["sha256"] == digest:
        return {"changed": False, "entry": current}
    with tempfile.TemporaryDirectory() as tmp:
        trial = Path(tmp) / "manifest.json"
        (Path(tmp) / entry["file"]).write_bytes(raw)
        trial.write_text(json.dumps({"masks": [entry]}), encoding="utf-8")
        mask = load_mask(region, trial)
    result = {"changed": True, "applied": False, "entry": entry, "bytes": len(raw)}
    result["mask"] = mask.provenance() | {"bounds": list(mask.geometry.bounds)}
    if not apply:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / entry["file"]).write_bytes(raw)
    entries[region] = entry
    # Older mask files stay in place as history; only the manifest pointer moves.
    path.write_text(
        json.dumps({"masks": [entries[r] for r in sorted(entries)]}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    result["applied"] = True
    return result
