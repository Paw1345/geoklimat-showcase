"""Read-only topology defect report over verified retained PIG spatial copies."""

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from ingestion.data_coverage import MODES, coverage_report
from ingestion.models import DataChannel
from ingestion.observations import retention_window
from ingestion.transport import FetchError

COPY_BATCH = 25
MAX_DEFECTS = 10000


def _defect(feature, row):
    from shapely.errors import GEOSException
    from shapely.geometry import shape
    from shapely.validation import explain_validity

    geometry = feature["geometry"]
    if geometry["type"] == "Point":
        polygons = []
    else:
        polygons = (
            [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        )
    if any(
        abs(a[0] - b[0]) > 180
        for polygon in polygons
        for ring in polygon
        for a, b in zip(ring[:-1], ring[1:], strict=True)
    ):
        reason, detail = "antimeridian", "Edge longer than 180 degrees"
    else:
        try:
            value = shape(geometry)
            if value.is_valid and not value.is_empty:
                return None
            reason, detail = "topology", explain_validity(value)
        except (GEOSException, ValueError, TypeError):
            reason, detail = "operation", "Shapely could not evaluate geometry"
    return {
        "object_id": feature["id"],
        "page": row["summary"]["page"],
        "snapshot_id": row["pk"],
        "sha256": row["sha256"],
        "fetched_at": row["fetched_at"].isoformat(),
        "reason": reason,
        "detail": detail,
    }


def _batches(rows, budget):
    """Consecutive pages of at most COPY_BATCH copies and ``budget`` stored bytes.

    A single copy above the budget stays alone so it is reported as refused, never skipped.
    """
    batches, current, size = [], [], 0
    for row in rows:
        if current and (len(current) >= COPY_BATCH or size + row["size_bytes"] > budget):
            batches.append(current)
            current, size = [], 0
        current.append(row)
        size += row["size_bytes"]
    if current:
        batches.append(current)
    return batches


def report(channel_id, harvest_id=None, now=None):
    """Every available verified copy is checked; nothing is repaired or written."""
    now = now or timezone.now()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        channel = DataChannel.objects.get(pk=channel_id)
        if channel.kind != "geojson" or channel.config.get("mode") not in MODES:
            raise FetchError("Reviewed PIG spatial channel required")
        harvests = channel.harvests.defer("page_fingerprints", "visited_urls")
        harvest = harvests.filter(pk=harvest_id).first() if harvest_id else harvests.first()
        if not harvest:
            raise FetchError("No retained spatial scan")
        harvest.channel = channel
        baseline = coverage_report(harvest, now)
        if baseline["issues"]:
            raise FetchError("Spatial scan metadata invalid: " + ", ".join(baseline["issues"]))
        rows = sorted(
            (
                row
                for row in channel.snapshots.filter(
                    summary__harvest_id=harvest.pk, download_job__isnull=True
                ).values("pk", "summary", "expires_at", "fetched_at", "sha256", "size_bytes")
                if row["expires_at"] > now and row["fetched_at"] + retention_window() > now
            ),
            key=lambda row: row["summary"]["page"],
        )
        defects, checked, batches = [], 0, []

        def page(data, row):
            nonlocal checked
            for feature in data["features"]:
                checked += 1
                defect = _defect(feature, row)
                if defect and len(defects) < MAX_DEFECTS:
                    defects.append(defect)

        budget = min(settings.DATA_MANUAL_MAX_BYTES, 50 * 1024 * 1024)
        for batch in _batches(rows, budget):
            pages = [row["summary"]["page"] for row in batch]
            before = checked, len(defects)
            try:
                result = coverage_report(
                    harvest,
                    now,
                    True,
                    on_page=page,
                    max_payload_bytes=budget,
                    copy_ids=[row["pk"] for row in batch],
                )
                status = result["payload_check"]
            except FetchError as exc:
                status = "refused: " + str(exc)
            if status != "verified_available":
                # A failed batch contributes no partial evidence.
                checked, defects[:] = before[0], defects[: before[1]]
            batches.append({"pages": [min(pages), max(pages)], "payload_check": status})
    verified = [b for b in batches if b["payload_check"] == "verified_available"]
    return {
        "channel_id": channel.pk,
        "channel": channel.slug,
        "harvest_id": harvest.pk,
        "harvest_status": harvest.status,
        "as_of": now.isoformat(),
        "read_pages": harvest.pages,
        "available_pages": len(rows),
        "verified_batches": len(verified),
        "batches": batches,
        "features_checked": checked,
        "invalid_features": len(defects),
        "defects_truncated": len(defects) >= MAX_DEFECTS,
        "defects": defects,
        "complete_scope_checked": (
            harvest.status == "complete"
            and bool(rows)
            and len(rows) == harvest.pages
            and len(verified) == len(batches)
        ),
        "repaired": False,
        "data_modified": False,
        "topology_model": "Shapely/GEOS is_valid, planar WGS84",
    }
