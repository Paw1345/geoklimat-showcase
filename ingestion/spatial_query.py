"""Bounded polygon/bbox search over verified retained copies; no remote retrieval."""

import hashlib
import json
import math

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from ingestion.data_coverage import MODES, coverage_report
from ingestion.models import DataChannel
from ingestion.spatial_geometry import METHODS, GeometryError, GeometryFilter
from ingestion.transport import FetchError

RESULT_BYTES = 5 * 1024 * 1024


def valid_bbox(value):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(type(x) not in {int, float} or not math.isfinite(x) for x in value)
    ):
        raise FetchError("Invalid WGS84 bbox")
    west, south, east, north = value
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise FetchError("Invalid WGS84 bbox")
    return tuple(float(x) for x in value)


def envelope(geometry):
    polygons = (
        [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    )
    points = [p for polygon in polygons for ring in polygon for p in ring]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def overlaps(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def query(
    channel_id,
    bbox,
    limit=100,
    *,
    copy_page=None,
    copy_token="",
    result_page=1,
    method="envelope",
):
    bbox = valid_bbox(bbox)
    if not isinstance(method, str) or method not in METHODS:
        raise FetchError("Invalid spatial intersection method")
    geometry_filter = GeometryFilter(bbox) if method == "geometry" else None
    if type(limit) is not int or not 1 <= limit <= 500:
        raise FetchError("Spatial result limit invalid")
    if (
        (copy_page is not None and (type(copy_page) is not int or not 1 <= copy_page <= 10000))
        or not isinstance(copy_token, str)
        or len(copy_token) > 2048
        or (copy_token and copy_page is None)
    ):
        raise FetchError("Invalid spatial batch request")
    if (
        type(result_page) is not int
        or not 1 <= result_page <= 100000
        or (result_page > 1 and (copy_page is None or not copy_token))
    ):
        raise FetchError("Further result pages require a pinned spatial batch")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        channel = DataChannel.objects.get(pk=channel_id)
        if (
            not channel.access_approved
            or channel.kind != "geojson"
            or channel.config.get("mode") not in MODES
        ):
            raise FetchError("Reviewed spatial channel required")
        now = timezone.now()
        from ingestion.pig_paging import validate_profile
        from ingestion.spatial_batches import prepare, read_token

        validate_profile(channel)
        pinned = read_token(copy_token, channel, bbox, limit, now, method=method)
        harvests = channel.harvests.defer("page_fingerprints", "visited_urls")
        harvest = harvests.filter(pk=pinned["harvest"]).first() if pinned else harvests.first()
        if not harvest:
            raise FetchError("No retained spatial scan")
        harvest.channel = channel
        copy_ids, batching = None, None
        if copy_page is not None:
            baseline = coverage_report(harvest, now)
            if baseline["issues"]:
                raise FetchError("Spatial batch scan metadata invalid")
            copy_ids, batching = prepare(
                channel,
                harvest,
                now,
                bbox,
                limit,
                copy_page,
                copy_token,
                pinned,
                method=method,
            )
        matches, features, receipts, encoded_size = 0, [], [], 0
        geometry_error = None
        offset = (result_page - 1) * limit

        def page(data, row):
            nonlocal matches, encoded_size, geometry_error
            receipts.append(
                {
                    "snapshot_id": row["pk"],
                    "page": row["summary"]["page"],
                    "sha256": row["sha256"],
                    "fetched_at": row["fetched_at"].isoformat(),
                    "expires_at": row["expires_at"].isoformat(),
                }
            )
            for feature in data["features"]:
                try:
                    matched = (
                        geometry_filter.matches(feature["geometry"])
                        if geometry_filter
                        else overlaps(envelope(feature["geometry"]), bbox)
                    )
                except GeometryError as exc:
                    geometry_error = exc
                    raise
                if matched:
                    matches += 1
                    if matches > offset and len(features) < limit:
                        encoded_size += len(
                            json.dumps(feature, ensure_ascii=False, allow_nan=False).encode()
                        )
                        if encoded_size > RESULT_BYTES:
                            raise FetchError("Spatial results exceed output budget")
                        features.append(feature)

        coverage = coverage_report(
            harvest,
            now,
            True,
            on_page=page,
            max_payload_bytes=min(settings.DATA_MANUAL_MAX_BYTES, 50 * 1024 * 1024),
            copy_ids=copy_ids,
        )
        if coverage["payload_check"] != "verified_available":
            if geometry_error is not None:
                raise geometry_error
            raise FetchError("Retained spatial copies failed verification or unavailable")
        result_pages = max(1, (matches + limit - 1) // limit)
        if result_page > result_pages:
            raise FetchError("Spatial result page unavailable")
        result = {
            "type": "FeatureCollection",
            "features": features,
            "geoklimat_query": {
                "channel_id": channel.pk,
                "channel": channel.name,
                "harvest_id": harvest.pk,
                "as_of": now.isoformat(),
                "bbox": bbox,
                "crs": "EPSG:4326",
                "method": (
                    "planar_geometry_bbox_intersection"
                    if geometry_filter
                    else "geometry_envelope_overlap"
                ),
                "exact_geometry_intersection": bool(geometry_filter),
                "intersection_model": "planar_WGS84_coordinates" if geometry_filter else None,
                "topology_verified_features": geometry_filter.checked if geometry_filter else 0,
                "geometry_positions_checked": geometry_filter.positions if geometry_filter else 0,
                "geodesic": False,
                "administrative_mask_verified": False,
                "clipped": False,
                "matches_in_available_copies": matches,
                "matches_scope": "selected_copy_batch" if batching else "all_available_copies",
                "global_match_count": None if batching else matches,
                "copy_batch": batching,
                "result_pagination": {
                    "page": result_page,
                    "pages": result_pages,
                    "page_size": limit,
                    "first": offset + 1 if features else 0,
                    "last": offset + len(features) if features else 0,
                    "has_previous": bool(batching and result_page > 1),
                    "has_next": bool(batching and result_page < result_pages),
                    "scope": "selected_copy_batch" if batching else "all_available_copies",
                    "order": "OBJECTID_ascending",
                },
                "returned": len(features),
                "truncated": matches > len(features),
                "limit": limit,
                "full_scope_verified": coverage["scope_verified"],
                "coverage": coverage,
                "receipts": receipts,
                "attribution": channel.attribution,
                "legal_status_verified": False,
            },
        }
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
        if len(payload) > RESULT_BYTES:
            raise FetchError("Spatial results exceed output budget")
        return result, payload, hashlib.sha256(payload).hexdigest()
