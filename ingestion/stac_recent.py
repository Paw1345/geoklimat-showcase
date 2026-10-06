"""Reviewed 24-hour head samples, independent of historical STAC cursors."""

import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from django.utils import timezone

from ingestion.models import DataChannel
from ingestion.pig_data import validate_geometry
from ingestion.stac_coverage import aware
from ingestion.transport import FetchError

MODE = "stac_recent"
URL = "https://stac.dataspace.copernicus.eu/v1/search"
PROFILES = {
    "copernicus-scenes-pl": ("PL", [14.12, 49.0, 24.15, 54.84]),
    "copernicus-scenes-europe": ("EU", [-31.3, 27.5, 35.7, 70.1]),
}


def configuration(parent):
    from ingestion.data_paging import digest

    region, bbox = PROFILES.get(parent.slug, (None, None))
    expected = {"bbox": bbox, "collection": "sentinel-2-l2a", "pagination": "stac_get"}
    if (
        parent.kind != "stac"
        or parent.region != region
        or parent.url != URL
        or parent.config != expected
        or not parent.access_approved
        or not parent.enabled
    ):
        raise FetchError("STAC recent needs unchanged reviewed active history profile")
    return {
        **expected,
        "mode": MODE,
        "head_window_hours": 24,
        "head_max_pages": 2,
        "history_channel_id": parent.pk,
        "history_config_hash": digest([parent.url, parent.kind, parent.region, parent.config]),
    }


def validate_profile(channel):
    parent_id = channel.config.get("history_channel_id")
    if type(parent_id) is not int or parent_id <= 0:
        raise FetchError("STAC recent history identity invalid")
    try:
        parent = DataChannel.objects.get(pk=parent_id)
    except DataChannel.DoesNotExist as exc:
        raise FetchError("STAC recent history missing") from exc
    if (
        channel.kind != "stac"
        or channel.url != URL
        or channel.region != parent.region
        or channel.registry_id != parent.registry_id
        or channel.slug != parent.slug + "-recent"
        or channel.config != configuration(parent)
    ):
        raise FetchError("STAC recent profile changed")
    return parent


def validate_request(channel, url, max_age=timedelta(minutes=10)):
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    params = dict(pairs)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "stac.dataspace.copernicus.eu"
        or parsed.path != "/v1/search"
        or parsed.fragment
        or len(params) != len(pairs)
        or set(params)
        not in (
            {"bbox", "limit", "sortby", "collections", "datetime"},
            {"bbox", "limit", "sortby", "collections", "datetime", "token"},
        )
        or params.get("bbox") != ",".join(map(str, channel.config["bbox"]))
        or params.get("limit") != "10"
        or params.get("sortby") != "-datetime"
        or params.get("collections") != channel.config["collection"]
    ):
        raise FetchError("STAC recent request left reviewed profile")
    dates = params.get("datetime", "").split("/")
    begin, end = (aware(dates[0]), aware(dates[1])) if len(dates) == 2 else (None, None)
    now = timezone.now()
    if (
        not begin
        or not end
        or end - begin != timedelta(hours=24)
        or not now - max_age <= end <= now
    ):
        raise FetchError("STAC recent window invalid")
    return begin, end


def validate_page(channel, payload, summary, max_age=timedelta(minutes=10)):
    begin, end = validate_request(channel, summary["request_url"], max_age=max_age)
    features = json.loads(payload)["features"]
    if not 0 <= summary["records"] == summary["raw_records"] <= 10:
        raise FetchError("STAC recent page size or dates invalid")
    for feature in features:
        stamp = aware(feature.get("properties", {}).get("datetime"))
        if (
            feature.get("type") != "Feature"
            or not isinstance(feature.get("id"), str)
            or not feature["id"]
            or len(feature["id"]) > 200
            or feature.get("collection") != channel.config["collection"]
            or not stamp
            or not begin <= stamp <= end
        ):
            raise FetchError("STAC recent feature scope invalid")
        validate_geometry(feature.get("geometry"))


def probe(channel, client):
    from ingestion.data_paging import initial_url, validate_page_url
    from ingestion.data_service import collect

    validate_profile(channel)
    client.max_bytes = settings.DATA_AUTO_MAX_BYTES
    start = initial_url(channel)
    url, count, total, clocks, seen, fingerprints = start, 0, 0, [], set(), set()
    for _ in range(2):
        validate_page_url(start, url, {"token"})
        if url in seen:
            raise FetchError("STAC recent probe loop")
        seen.add(url)
        payload, _, summary = collect(channel, client, request_url=url)
        validate_page_url(url, summary["request_url"], set())
        if summary["raw_records"] and summary["page_fingerprint"] in fingerprints:
            raise FetchError("STAC recent probe repeated page")
        fingerprints.add(summary["page_fingerprint"])
        count += summary["records"]
        total += len(payload)
        if summary.get("oldest_observation"):
            clocks.extend(
                [aware(summary["oldest_observation"]), aware(summary["newest_observation"])]
            )
        url = summary["next_url"]
        if not url:
            break
    if url:
        validate_page_url(start, url, {"token"})
        if url in seen:
            raise FetchError("STAC recent probe next loop")
    return {
        "records": count,
        "bytes": total,
        "pages": len(seen),
        "more_available": bool(url),
        "oldest_observation": min(clocks).isoformat() if clocks else None,
        "newest_observation": max(clocks).isoformat() if clocks else None,
        "full_day_coverage_verified": False,
    }
