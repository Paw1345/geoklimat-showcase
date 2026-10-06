"""Allowlisted GIS projection of a verified local STAC batch, never assets."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from ingestion.models import DataChannel
from ingestion.spatial_query import envelope, overlaps, valid_bbox
from ingestion.stac_integrity import verify_batch
from ingestion.transport import FetchError

RESULT_BYTES = 5 * 1024 * 1024


def query(channel_id, harvest_id, start=1, limit=100, scene="", since=None, until=None, bbox=None):
    if type(limit) is not int or not 1 <= limit <= 250:
        raise FetchError("Invalid scene limit")
    if not isinstance(scene, str) or len(scene) > 200:
        raise FetchError("Invalid scene identity")
    for stamp in (since, until):
        if stamp is not None and stamp.utcoffset() is None:
            raise FetchError("Scene filter needs timezone")
    if since is not None and until is not None and since > until:
        raise FetchError("Scene date filter reversed")
    bbox = valid_bbox(bbox) if bbox is not None else None
    features, matched = [], 0
    projected_bytes = 0

    def collect(feature, stamp, snapshot_id, page, fingerprint):
        nonlocal matched, projected_bytes
        if scene and feature["id"] != scene:
            return
        if (since is not None and stamp < since) or (until is not None and stamp > until):
            return
        if bbox is not None and not overlaps(envelope(feature["geometry"]), bbox):
            return
        matched += 1
        if len(features) >= limit:
            return
        projected = {
            "type": "Feature",
            "id": f"{snapshot_id}:{feature['id']}",
            "geometry": feature["geometry"],
            "properties": {
                "scene_id": feature["id"],
                "collection": feature["collection"],
                "datetime": stamp.isoformat(),
                "snapshot_id": snapshot_id,
                "page": page,
                "source_feature_sha256": fingerprint,
            },
        }
        projected_bytes += len(json.dumps(projected, allow_nan=False).encode())
        if projected_bytes > RESULT_BYTES:
            raise FetchError("Scene output budget exceeded")
        features.append(projected)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        channel = DataChannel.objects.get(pk=channel_id, kind="stac")
        harvest = channel.harvests.defer("visited_urls", "page_fingerprints").get(pk=harvest_id)
        now = timezone.now()
        verification = verify_batch(channel, harvest, now, start, on_scene=collect)
    result = {
        "type": "FeatureCollection",
        "features": features,
        "geoklimat": {
            "as_of": now,
            "channel_id": channel.pk,
            "harvest_id": harvest.pk,
            "region": channel.region,
            "verification": verification,
            "filters": {"scene": scene, "since": since, "until": until, "bbox": bbox},
            "matched_occurrences_in_batch": matched,
            "returned_occurrences": len(features),
            "truncated": matched > len(features),
            "limit": limit,
            "spatial_method": "geometry_envelope_overlap" if bbox is not None else "not_filtered",
            "occurrences_deduplicated": False,
            "assets_downloaded": False,
            "read_only": True,
        },
    }
    payload = json.dumps(
        result, cls=DjangoJSONEncoder, ensure_ascii=False, allow_nan=False
    ).encode()
    if len(payload) > RESULT_BYTES:
        raise FetchError("Scene output budget exceeded")
    return result, payload, hashlib.sha256(payload).hexdigest()
