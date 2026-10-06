"""Read-only PIG identity-scan coverage; metadata availability is not payload integrity."""

import hashlib
import json

from django.conf import settings
from django.db.models import BigIntegerField, Func
from django.utils import timezone

from ingestion.data_paging import digest
from ingestion.models import DataSnapshot
from ingestion.observations import retention_window
from ingestion.pig_data import validate_geometry
from ingestion.transport import FetchError

MODES = {"pig_midas_scan", "pig_spatial_scan"}


def coverage_report(
    harvest,
    now=None,
    verify_payloads=False,
    *,
    on_page=None,
    max_payload_bytes=None,
    copy_ids=None,
):
    now = now or timezone.now()
    channel = harvest.channel
    if channel.config.get("mode") not in MODES:
        raise FetchError("Coverage supports only reviewed PIG identity scans")
    rows = list(
        channel.snapshots.filter(summary__harvest_id=harvest.pk, download_job__isnull=True)
        .order_by("pk")
        .values("pk", "summary", "expires_at", "fetched_at", "size_bytes", "media_type", "sha256")[
            :10001
        ]
    )
    issues = set()
    available = []
    seen = set()
    totals = set()
    limit = channel.config.get("feature_limit")
    if type(limit) is not int or limit <= 0 or len(rows) > 10000:
        issues.add("budget_or_profile")
        rows = []
    for row in rows:
        s = row["summary"]
        if not isinstance(s, dict):
            issues.add("invalid_metadata")
            continue
        page, records, total = s.get("page"), s.get("records"), s.get("source_count")
        if (
            s.get("collector") != channel.config["mode"]
            or s.get("identity_scope_hash") != harvest.config_hash
            or type(page) is not int
            or not 1 <= page <= harvest.pages
            or page in seen
            or type(records) is not int
            or not 0 < records <= limit
            or type(total) is not int
            or not 0 < total <= 100000
        ):
            issues.add("invalid_metadata")
            continue
        seen.add(page)
        totals.add(total)
        if row["expires_at"] > now and row["fetched_at"] + retention_window() > now:
            available.append(row)
    total = next(iter(totals)) if len(totals) == 1 else None
    if len(totals) > 1:
        issues.add("mixed_scope_counts")
    available_records = sum(row["summary"]["records"] for row in available)
    available_bytes = sum(row["size_bytes"] for row in available)
    if (total is not None and total < harvest.records) or available_records > harvest.records:
        issues.add("inconsistent_record_count")
    complete = harvest.status == "complete" and not harvest.next_url
    full = (
        complete
        and not issues
        and total == harvest.records
        and len(available) == harvest.pages
        and available_records == harvest.records
    )
    report = {
        "channel": channel.slug,
        "harvest_id": harvest.pk,
        "as_of": now.isoformat(),
        "status": harvest.status,
        "scan_complete": complete,
        "read_pages": harvest.pages,
        "read_records": harvest.records,
        "source_count": total,
        "available_pages": len(available),
        "available_records": available_records,
        "available_bytes": available_bytes,
        "export_limit_bytes": settings.DATA_MANUAL_MAX_BYTES,
        "export_eligible": full and available_bytes <= settings.DATA_MANUAL_MAX_BYTES,
        "unavailable_pages": max(0, harvest.pages - len(available)),
        "full_metadata_coverage": full,
        "issues": sorted(issues),
        "payload_check": "not_checked",
        "scope_verified": False,
    }
    if verify_payloads:
        selected = available
        if copy_ids is not None:
            if (
                on_page is None
                or not isinstance(copy_ids, list)
                or not 1 <= len(copy_ids) <= 25
                or any(type(pk) is not int or pk < 1 for pk in copy_ids)
                or len(set(copy_ids)) != len(copy_ids)
            ):
                raise FetchError("Invalid spatial copy selection")
            selected = [row for row in available if row["pk"] in set(copy_ids)]
            if len(selected) != len(copy_ids):
                raise FetchError("Selected spatial copies unavailable")
            report.update(
                selected_pages=len(selected),
                all_available_copies_selected=len(selected) == len(available),
            )
        if on_page is not None:
            if issues:
                raise FetchError("Spatial query metadata invalid")
            if (
                type(max_payload_bytes) is not int
                or not 0 < max_payload_bytes <= settings.DATA_MANUAL_MAX_BYTES
                or sum(row["size_bytes"] for row in selected) > max_payload_bytes
            ):
                raise FetchError("Spatial query exceeds manual budget")
            # Check actual stored byte lengths before fetching any bytea payload.
            sizes = list(
                DataSnapshot.objects.filter(pk__in=[row["pk"] for row in selected])
                .annotate(
                    actual_bytes=Func(
                        "payload", function="OCTET_LENGTH", output_field=BigIntegerField()
                    )
                )
                .values_list("pk", "actual_bytes")
            )
            declared = {row["pk"]: row["size_bytes"] for row in selected}
            if (
                len(sizes) != len(selected)
                or any(
                    actual != declared[pk] or not 0 < actual <= settings.DATA_AUTO_MAX_BYTES
                    for pk, actual in sizes
                )
                or sum(actual for _, actual in sizes) > max_payload_bytes
            ):
                raise FetchError("Spatial query actual stored bytes exceed size/budget")
        report.update(
            _verify(harvest, selected, full and len(selected) == len(available), on_page=on_page)
        )
    elif copy_ids is not None:
        raise FetchError("Copy selection requires payload verification")
    return report


def _verify(harvest, rows, full, on_page=None):
    """Stream one bounded page at a time. Never retrieve missing/expired copies."""
    ids = []
    try:
        from ingestion.pig_paging import validate_profile

        validate_profile(harvest.channel)
        for row in sorted(rows, key=lambda r: r["summary"]["page"]):
            if not 0 < row["size_bytes"] <= settings.DATA_AUTO_MAX_BYTES:
                raise FetchError("invalid_size")
            snapshot = DataSnapshot.objects.only("payload").get(pk=row["pk"])
            raw = bytes(snapshot.payload)
            if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise FetchError("checksum_mismatch")
            data = json.loads(raw)
            features = data.get("features")
            p = data.get("geoklimat_provenance")
            if (
                row["media_type"] != "application/json"
                or data.get("type") != "FeatureCollection"
                or not isinstance(features, list)
                or len(features) != row["summary"]["records"]
                or not isinstance(p, dict)
                or p.get("identity_scope_hash") != harvest.config_hash
                or p.get("source_count") != row["summary"]["source_count"]
            ):
                raise FetchError("payload_scope_mismatch")
            for feature in features:
                identity = feature.get("id")
                if (
                    feature.get("type") != "Feature"
                    or type(identity) is not int
                    or not 0 <= identity <= 2**53
                    or (ids and identity <= ids[-1])
                    or type(feature.get("properties", {}).get("OBJECTID")) is not int
                    or feature.get("properties", {}).get("OBJECTID") != identity
                ):
                    raise FetchError("payload_identity_mismatch")
                validate_geometry(feature.get("geometry"))
                ids.append(identity)
            if on_page:
                on_page(data, row)
        channel = harvest.channel
        if (
            full
            and digest([channel.url, channel.kind, channel.region, channel.config, ids])
            != harvest.config_hash
        ):
            raise FetchError("scope_hash_mismatch")
    except (FetchError, ValueError, TypeError, AttributeError, DataSnapshot.DoesNotExist):
        return {"payload_check": "failed", "scope_verified": False}
    return {
        "payload_check": "verified_available" if rows else "no_available_pages",
        "scope_verified": bool(full and rows),
    }


def write_verified_export(harvest, now, stream):
    """Caller holds a read-only consistent transaction. Output is temporary, never cache."""
    report = coverage_report(harvest, now)
    if not report["full_metadata_coverage"]:
        raise FetchError("Full copy coverage unavailable")
    rows = list(
        harvest.channel.snapshots.filter(summary__harvest_id=harvest.pk, download_job__isnull=True)
        .order_by("pk")
        .values("pk", "summary", "size_bytes", "media_type", "sha256", "fetched_at", "expires_at")
    )
    if sum(row["size_bytes"] for row in rows) > settings.DATA_MANUAL_MAX_BYTES:
        raise FetchError("Combined export exceeds manual budget")
    hasher = hashlib.sha256()
    provenance = {}
    receipts = []
    first = True

    def write(raw):
        if stream.tell() + len(raw) > settings.DATA_MANUAL_MAX_BYTES:
            raise FetchError("Combined export exceeds manual budget")
        stream.write(raw)
        hasher.update(raw)

    def encode(value):
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()

    def page(data, row):
        nonlocal first
        if not provenance:
            provenance.update(data["geoklimat_provenance"])
        receipts.append(
            {
                "page": row["summary"]["page"],
                "source_url": data["geoklimat_provenance"].get("source_url"),
                "copy_acquired_at": row["fetched_at"].isoformat(),
                "copy_expires_at": min(
                    row["expires_at"], row["fetched_at"] + retention_window()
                ).isoformat(),
            }
        )
        for feature in data["features"]:
            write((b"" if first else b",") + encode(feature))
            first = False

    write(b'{"type":"FeatureCollection","features":[')
    verified = _verify(harvest, rows, True, page)
    if not verified["scope_verified"]:
        raise FetchError("Combined export integrity or scope verification failed")
    provenance.update(
        source_url=harvest.channel.url,
        partial=False,
        coverage_basis="complete pinned identity manifest, not institutional coverage",
        transformation="merge verified exact-ID pages; supplier WGS84; not point-in-time",
        harvest_id=harvest.pk,
        export_created_at=now.isoformat(),
        copy_receipts=receipts,
    )
    write(b'],"geoklimat_provenance":' + encode(provenance) + b"}")
    return hasher.hexdigest()
