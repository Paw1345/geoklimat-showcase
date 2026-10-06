"""Read-only integrity of one explicitly selected, bounded STAC page batch."""

import hashlib
import json

from django.conf import settings
from django.db.models import BigIntegerField, F, Func

from ingestion.data_paging import validate_page_url
from ingestion.models import DataSnapshot
from ingestion.observations import retention_window
from ingestion.pig_data import validate_geometry
from ingestion.stac_coverage import aware, search_scope
from ingestion.transport import FetchError

PAGE_COUNT = 25


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FetchError("STAC duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(value):
    raise FetchError("STAC non-finite JSON constant")


def verify_batch(channel, harvest, now, start=1, on_scene=None, stop=None):
    if not channel.access_approved:
        raise FetchError("STAC access not approved")
    scope = search_scope(channel, harvest)
    if type(start) is not int or not 1 <= start <= harvest.pages <= 10000:
        raise FetchError("STAC batch page range invalid")
    if stop is not None and (type(stop) is not int or not start <= stop <= harvest.pages):
        raise FetchError("STAC frozen batch boundary invalid")
    end = min(harvest.pages, start + PAGE_COUNT - 1, stop if stop is not None else harvest.pages)
    rows = list(
        channel.snapshots.filter(
            summary__harvest_id=harvest.pk,
            summary__page__gte=start,
            summary__page__lte=end,
            download_job__isnull=True,
        )
        .order_by("pk")
        .annotate(
            actual_bytes=Func(
                F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField()
            ),
        )
        .values(
            "pk",
            "summary",
            "size_bytes",
            "actual_bytes",
            "sha256",
            "media_type",
            "fetched_at",
            "expires_at",
        )[: PAGE_COUNT + 1]
    )
    pages = [row["summary"].get("page") for row in rows]
    if (
        len(rows) != end - start + 1
        or any(type(p) is not int for p in pages)
        or set(pages) != set(range(start, end + 1))
    ):
        raise FetchError("STAC batch missing or duplicate copies")
    total_bytes = sum(row["actual_bytes"] for row in rows)
    if total_bytes > min(settings.DATA_MANUAL_MAX_BYTES, 50 * 1024 * 1024) or any(
        not 0 < row["actual_bytes"] <= settings.DATA_AUTO_MAX_BYTES
        or row["actual_bytes"] != row["size_bytes"]
        for row in rows
    ):
        raise FetchError("STAC batch actual size exceeds budget or declaration")
    # Validate every metadata row before reading any payload.
    for row in rows:
        s = row["summary"]
        records, raw_records = s.get("records"), s.get("raw_records")
        if (
            row["media_type"] != "application/json"
            or s.get("kind") != "stac"
            or s.get("scope") != channel.region
            or s.get("bbox") != scope["bbox"]
            or s.get("assets_downloaded") is not False
            or type(records) is not int
            or type(raw_records) is not int
            or not 0 <= records <= raw_records <= 10
            or row["expires_at"] <= now
            or row["fetched_at"] > now
            or row["fetched_at"] + retention_window() <= now
        ):
            raise FetchError("STAC batch metadata or copy expiry invalid")
        validate_page_url(harvest.start_url, s.get("request_url", ""), {"token"})
    receipts, stamps, identities = [], [], {}
    occurrences = 0
    for row in sorted(rows, key=lambda r: r["summary"]["page"]):
        raw = bytes(DataSnapshot.objects.only("payload").get(pk=row["pk"]).payload)
        if len(raw) != row["actual_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise FetchError("STAC batch SHA256 mismatch")
        try:
            data = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            features = data.get("features")
            if (
                data.get("type") != "FeatureCollection"
                or not isinstance(features, list)
                or len(features) != row["summary"]["records"]
            ):
                raise FetchError("STAC payload record count or schema mismatch")
            page_ids, page_stamps = set(), []
            for feature in features:
                identity = feature.get("id")
                properties = feature.get("properties")
                if (
                    feature.get("type") != "Feature"
                    or not isinstance(identity, str)
                    or not identity
                    or len(identity) > 200
                    or identity in page_ids
                    or feature.get("collection") != scope["collection"]
                    or not isinstance(properties, dict)
                ):
                    raise FetchError("STAC payload identity or collection mismatch")
                stamp = aware(properties.get("datetime"))
                if (
                    not stamp
                    or not scope["search_start"] <= stamp <= scope["search_end"]
                    or not now - retention_window() < stamp <= now
                ):
                    raise FetchError("STAC payload observation outside retained scope")
                validate_geometry(feature.get("geometry"))
                page_ids.add(identity)
                page_stamps.append(stamp)
                fingerprint = hashlib.sha256(
                    json.dumps(feature, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                identities.setdefault(identity, []).append(fingerprint)
                if on_scene is not None:
                    on_scene(feature, stamp, row["pk"], row["summary"]["page"], fingerprint)
            summary = row["summary"]
            if page_stamps and (
                min(page_stamps) != aware(summary.get("oldest_observation"))
                or max(page_stamps) != aware(summary.get("newest_observation"))
            ):
                raise FetchError("STAC payload observation bounds mismatch")
            if not page_stamps and (
                summary.get("oldest_observation") or summary.get("newest_observation")
            ):
                raise FetchError("STAC empty payload declares observation dates")
            stamps.extend(page_stamps)
            occurrences += len(features)
        except (ValueError, TypeError, AttributeError, RecursionError) as exc:
            raise FetchError("STAC payload JSON or schema invalid") from exc
        receipts.append(
            {
                "snapshot_id": row["pk"],
                "page": summary["page"],
                "sha256": row["sha256"],
                "bytes": len(raw),
            }
        )
    return {
        "channel_id": channel.pk,
        "harvest_id": harvest.pk,
        "start_page": start,
        "end_page": end,
        "checked_pages": len(rows),
        "checked_bytes": total_bytes,
        "record_occurrences_in_batch": occurrences,
        "distinct_scene_ids_in_batch": len(identities),
        "repeated_occurrences_in_batch": occurrences - len(identities),
        "scene_ids_with_multiple_variants_in_batch": sum(
            len(set(v)) > 1 for v in identities.values()
        ),
        "oldest_observation": min(stamps) if stamps else None,
        "newest_observation": max(stamps) if stamps else None,
        "receipts": receipts,
        "batch_verified": True,
        "all_harvest_pages_verified": False,
        "full_current_30_day_coverage_verified": False,
        "geographic_mask_verified": False,
        "next_start_page": end + 1 if end < (stop if stop is not None else harvest.pages) else None,
    }
