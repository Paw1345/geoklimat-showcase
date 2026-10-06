"""Bounded metadata-only evidence for frozen-window STAC GET scans."""

import math
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from django.utils.dateparse import parse_datetime

from ingestion.data_paging import digest, validate_page_url
from ingestion.observations import retention_window
from ingestion.stac_timeline import timeline
from ingestion.transport import FetchError


def aware(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    return parsed if parsed and parsed.utcoffset() is not None else None


def search_scope(channel, harvest):
    recent = isinstance(channel.config, dict) and channel.config.get("mode") == "stac_recent"
    bounded = isinstance(channel.config, dict) and channel.config.get("mode") == "stac_bounded"
    if bounded:
        from ingestion.stac_bounded import validate_profile

        validate_profile(channel)
    if recent:
        from ingestion.stac_recent import validate_profile

        validate_profile(channel)
    if (
        channel.kind != "stac"
        or channel.region not in {"PL", "EU"}
        or not isinstance(channel.config, dict)
        or channel.config.get("pagination") != "stac_get"
    ):
        raise FetchError("Unsupported STAC scan profile")
    if harvest.config_hash != digest([channel.url, channel.kind, channel.region, channel.config]):
        raise FetchError("STAC configuration differs from historical scan")
    base, start = urlsplit(channel.url), urlsplit(harvest.start_url)
    pairs = parse_qsl(start.query, keep_blank_values=True)
    params = dict(pairs)
    bbox = channel.config.get("bbox")
    if (
        start.scheme != "https"
        or start.username
        or start.password
        or start.fragment
        or (start.netloc, start.path) != (base.netloc, base.path)
        or len(params) != len(pairs)
        or set(params) != {"bbox", "limit", "sortby", "collections", "datetime"}
        or not isinstance(bbox, list)
        or len(bbox) != 4
        or any(type(x) not in {int, float} or not math.isfinite(x) for x in bbox)
        or not -180 <= bbox[0] < bbox[2] <= 180
        or not -90 <= bbox[1] < bbox[3] <= 90
        or params["bbox"] != ",".join(map(str, bbox))
        or params["collections"] != channel.config.get("collection")
        or params["limit"] != "10"
        or params["sortby"] != "-datetime"
    ):
        raise FetchError("STAC frozen scope invalid")
    dates = params["datetime"].split("/")
    start_time, end_time = (aware(dates[0]), aware(dates[1])) if len(dates) == 2 else (None, None)
    window = timedelta(hours=24) if recent or bounded else retention_window()
    if not start_time or not end_time or end_time - start_time != window:
        raise FetchError("STAC search interval invalid")
    return {
        "search_start": start_time,
        "search_end": end_time,
        "bbox": bbox,
        "collection": params["collections"],
        "acquisition_mode": "bounded_window"
        if bounded
        else "recent_sample"
        if recent
        else "history_scan",
        "search_window_hours": int(window.total_seconds() / 3600),
        "sample_page_limit": 2 if recent else None,
    }


def coverage(channel, harvest, now):
    result = {
        "channel_id": channel.pk,
        "name": channel.name,
        "region": channel.region,
        "harvest_id": harvest.pk if harvest else None,
        "status": harvest.status if harvest else "not_started",
        "read_pages": harvest.pages if harvest else 0,
        "read_record_occurrences": harvest.records if harvest else 0,
        "scan_complete": False,
        "has_cursor": bool(harvest and harvest.next_url),
        "available_pages": 0,
        "available_record_occurrences": 0,
        "unavailable_pages": harvest.pages if harvest else 0,
        "available_bytes": 0,
        "all_read_pages_present": False,
        "oldest_observation_metadata": None,
        "newest_observation_metadata": None,
        "issues": [],
        "payload_hashes_verified": False,
        "unique_scenes_verified": False,
        "geographic_mask_verified": False,
        "full_current_30_day_coverage_verified": False,
        "assets_downloaded": False,
        "stored_snapshot_rows": None,
        "missing_snapshot_pages": None,
        "missing_page_numbers_sample": [],
        "expired_snapshot_rows": None,
        "rejected_snapshot_rows": None,
        "window_timeline": None,
    }
    if not harvest:
        return result
    issues = set()
    try:
        result.update(search_scope(channel, harvest))
    except FetchError:
        result["issues"] = ["scope_or_profile_mismatch"]
        return result
    result["search_end_lag_seconds"] = max(0, int((now - result["search_end"]).total_seconds()))
    if not 0 <= harvest.pages <= 10000:
        result["issues"] = ["page_budget"]
        return result
    rows = list(
        channel.snapshots.filter(
            summary__harvest_id=harvest.pk,
            download_job__isnull=True,
        )
        .order_by("pk")
        .values("summary", "expires_at", "fetched_at", "size_bytes", "media_type")[:10001]
    )
    if len(rows) > 10000:
        result["issues"] = ["page_budget"]
        return result
    seen, stored_pages, available, stamps = set(), set(), [], []
    expired_rows = 0
    for row in rows:
        summary = row["summary"]
        if not isinstance(summary, dict):
            issues.add("invalid_page_metadata")
            continue
        page, records, raw = (summary.get(key) for key in ("page", "records", "raw_records"))
        if type(page) is int and 1 <= page <= harvest.pages:
            stored_pages.add(page)
        if (
            type(page) is not int
            or not 1 <= page <= harvest.pages
            or page in seen
            or type(records) is not int
            or type(raw) is not int
            or not 0 <= records <= raw <= 10
            or summary.get("kind") != "stac"
            or summary.get("scope") != channel.region
            or summary.get("bbox") != result["bbox"]
            or summary.get("assets_downloaded") is not False
            or row["media_type"] != "application/json"
            or not 0 < row["size_bytes"] <= settings.DATA_AUTO_MAX_BYTES
        ):
            issues.add("invalid_page_metadata")
            continue
        try:
            validate_page_url(harvest.start_url, summary.get("request_url", ""), {"token"})
        except (FetchError, TypeError, ValueError):
            issues.add("page_scope_mismatch")
            continue
        seen.add(page)
        if row["expires_at"] <= now or row["fetched_at"] + retention_window() <= now:
            expired_rows += 1
            continue
        if records:
            oldest, newest = (
                aware(summary.get("oldest_observation")),
                aware(summary.get("newest_observation")),
            )
            if (
                not oldest
                or not newest
                or not result["search_start"] <= oldest <= newest <= result["search_end"]
            ):
                issues.add("observation_bounds_invalid")
                continue
            if oldest <= now - retention_window() or newest > now:
                issues.add("observation_retention_review")
                continue
            stamps.extend((oldest, newest))
        available.append(row)
    complete = harvest.status == "complete" and not harvest.next_url
    if (harvest.status == "complete") != (not harvest.next_url):
        issues.add("cursor_status_mismatch")
    count = sum(row["summary"]["records"] for row in available)
    if count > harvest.records:
        issues.add("record_count_mismatch")
    result.update(
        scan_complete=complete,
        available_pages=len(available),
        available_record_occurrences=count,
        unavailable_pages=max(0, harvest.pages - len(available)),
        available_bytes=sum(row["size_bytes"] for row in available),
        all_read_pages_present=not issues
        and len(available) == harvest.pages
        and bool(harvest.pages),
        oldest_observation_metadata=min(stamps) if stamps else None,
        newest_observation_metadata=max(stamps) if stamps else None,
        issues=sorted(issues),
        stored_snapshot_rows=len(rows),
        missing_snapshot_pages=harvest.pages - len(stored_pages),
        missing_page_numbers_sample=sorted(set(range(1, harvest.pages + 1)) - stored_pages)[:20],
        expired_snapshot_rows=expired_rows,
        rejected_snapshot_rows=len(rows) - len(available) - expired_rows,
        window_timeline=timeline(result, now, None),
    )
    return result
