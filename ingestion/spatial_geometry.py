"""Local planar polygon/bbox predicates; never repair or transform provider geometry."""

from ingestion.pig_data import validate_geometry
from ingestion.transport import FetchError

METHODS = {"envelope", "geometry"}
MAX_QUERY_POSITIONS = 1_000_000


class GeometryError(FetchError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class GeometryFilter:
    def __init__(self, bbox):
        from shapely.geometry import box

        self.rectangle = box(*bbox)
        self.positions = 0
        self.checked = 0

    def matches(self, geometry):
        from shapely.errors import GEOSException
        from shapely.geometry import shape

        validate_geometry(geometry)
        polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        )
        for polygon in polygons:
            for ring in polygon:
                self.positions += len(ring)
                if self.positions > MAX_QUERY_POSITIONS:
                    raise GeometryError("budget", "Spatial geometry position budget exceeded")
                if any(abs(a[0] - b[0]) > 180 for a, b in zip(ring[:-1], ring[1:], strict=True)):
                    raise GeometryError(
                        "antimeridian", "Antimeridian geometry needs a separate reviewed scope"
                    )
        try:
            value = shape(geometry)
            if value.is_empty or not value.is_valid:
                raise GeometryError(
                    "topology", "Spatial geometry topology invalid; no automatic repair"
                )
            # Validate every selected feature, including those outside the rectangle
            # and after the output limit; no partial success on a later invalid ring.
            matched = value.intersects(self.rectangle)
        except (GEOSException, ValueError, TypeError) as exc:
            raise GeometryError("operation", "Spatial geometry operation failed") from exc
        self.checked += 1
        return bool(matched)
