"""Resumable bounded public GET scans; each page and cursor commit together."""

import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qsl, urljoin, urlsplit

from django.db import transaction
from django.utils import timezone

from ingestion.data_service import collect, query_url, store_snapshot
from ingestion.models import DataChannel, DataHarvest
from ingestion.transport import FetchError

RETENTION_REVIEW = "retention_review"
RETENTION_NOTE = (
    "Source page has records but none remain after retention/freshness filtering. "
    "Review the pinned window or timestamps; cursor preserved, not a complete scan."
)


def review_result(channel, harvest):
    return {
        "channel": channel.slug,
        "status": RETENTION_REVIEW,
        "harvest_id": harvest.pk,
        "pages": harvest.pages,
        "records": harvest.records,
        "bytes": harvest.size_bytes,
        "error": RETENTION_NOTE,
        "cursor_preserved": True,
        "scan_complete": False,
    }


def pause_retention(channel, harvest):
    baseline = (harvest.next_url, harvest.pages, harvest.records, harvest.size_bytes)
    with transaction.atomic():
        current = DataChannel.objects.select_for_update().get(pk=channel.pk)
        if (current.url, current.kind, current.region, current.config) != (
            channel.url,
            channel.kind,
            channel.region,
            channel.config,
        ) or not (current.enabled and current.access_approved):
            raise FetchError("STAC profile or approval changed; review state not written")
        locked = DataHarvest.objects.select_for_update().get(pk=harvest.pk)
        if (locked.next_url, locked.pages, locked.records, locked.size_bytes) != baseline:
            raise FetchError("STAC cursor changed; review state not written")
        locked.status, locked.error = RETENTION_REVIEW, RETENTION_NOTE
        locked.save(update_fields=["status", "error"])
    return review_result(current, locked)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_page_url(original, target, mutable):
    base, page = urlsplit(original), urlsplit(target)
    if (
        page.scheme != "https"
        or page.username
        or page.password
        or page.fragment
        or (page.netloc, page.path) != (base.netloc, base.path)
    ):
        raise FetchError("Data pagination left configured HTTPS resource")
    expected = [(key, value) for key, value in parse_qsl(base.query) if key not in mutable]
    actual = [(key, value) for key, value in parse_qsl(page.query) if key not in mutable]
    if sorted(expected) != sorted(actual):
        raise FetchError("Data pagination changed collection, time or spatial scope")


def initial_url(channel):
    if channel.kind == "geojson" and channel.config.get("pagination") == "wfs2":
        # Only reviewed WFS 2.0 profiles, never generic GeoJSON offsets.
        params = dict(parse_qsl(urlsplit(channel.url).query))
        if params.get("VERSION") != "2.0.0" or params.get("REQUEST") != "GetFeature":
            raise FetchError("WFS pagination requires a reviewed WFS 2.0 GetFeature URL")
        return query_url(
            channel.url, STARTINDEX=0, COUNT=int(channel.config.get("feature_limit", 100))
        )
    if channel.kind == "stac" and channel.config.get("pagination") == "stac_get":
        recent = channel.config.get("mode") == "stac_recent"
        if recent:
            from ingestion.stac_recent import validate_profile

            validate_profile(channel)
        now = timezone.now()
        bbox, collection = channel.config.get("bbox"), channel.config.get("collection")
        if not isinstance(bbox, list) or len(bbox) != 4 or not collection:
            raise FetchError("STAC requires reviewed bbox and collection")
        return query_url(
            channel.url,
            bbox=",".join(map(str, bbox)),
            limit=10,
            sortby="-datetime",
            collections=collection,
            datetime=(now - (timedelta(hours=24) if recent else timedelta(days=30))).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            + "/"
            + now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    raise FetchError("Unknown data pagination profile")


def page_metadata(channel, url, data):
    features = data["features"]
    ids = [row.get("id") for row in features]
    if any(not isinstance(key, (str, int)) for key in ids) or len(set(ids)) != len(ids):
        raise FetchError("Paged features require unique stable IDs")
    next_url = ""
    if channel.config["pagination"] == "stac_get" and channel.kind == "stac":
        links = [link for link in data.get("links", []) if link.get("rel") == "next"]
        if len(links) > 1 or any(link.get("method", "GET") != "GET" for link in links):
            raise FetchError("Unsupported STAC next-page method or ambiguity")
        if links:
            next_url = urljoin(url, links[0]["href"])
            validate_page_url(url, next_url, {"token"})
            if not features:
                raise FetchError("Empty STAC page with continuation")
    elif channel.config["pagination"] == "wfs2" and channel.kind == "geojson":
        params = dict(parse_qsl(urlsplit(url).query))
        size, offset = int(params.get("COUNT", 100)), int(params.get("STARTINDEX", 0))
        if size < 1 or len(features) > size:
            raise FetchError("WFS ignored requested page size")
        matched = data.get("numberMatched")
        if isinstance(matched, int) and matched > offset + len(features) and not features:
            raise FetchError("WFS returned empty page before reported end")
        more = (
            (offset + len(features) < matched)
            if isinstance(matched, int)
            else len(features) == size
        )
        if more:
            next_url = query_url(url, STARTINDEX=offset + len(features))
    else:
        raise FetchError("Unsupported data pagination")
    return {
        "next_url": next_url,
        "request_url": url,
        "page_fingerprint": digest(sorted(map(str, ids))),
        "raw_records": len(ids),
    }


def advance_harvest(channel, client):
    """Caller holds ingestion batch lock. Network outside transaction; page+cursor atomic."""
    if channel.config.get("mode") == "stac_bounded":
        from ingestion.stac_bounded import advance

        return advance(channel, client)
    if channel.config.get("mode") in {"pig_midas_scan", "pig_spatial_scan"}:
        from ingestion.pig_paging import advance

        return advance(channel, client)
    config_hash = digest([channel.url, channel.kind, channel.region, channel.config])
    recent = channel.config.get("mode") == "stac_recent"
    if recent:
        from ingestion.stac_recent import validate_profile

        validate_profile(channel)
    harvest = (
        None
        if recent
        else channel.harvests.filter(status__in=["running", "failed", RETENTION_REVIEW]).first()
    )
    if harvest and harvest.config_hash != config_hash:
        harvest.status, harvest.finished_at = "superseded", timezone.now()
        harvest.save(update_fields=["status", "finished_at"])
        harvest = None
    if harvest and harvest.status == RETENTION_REVIEW:
        return review_result(channel, harvest)
    if not harvest:
        url = initial_url(channel)
        harvest = DataHarvest.objects.create(
            channel=channel, config_hash=config_hash, start_url=url, next_url=url
        )
    try:
        for page_index in range(2):
            if harvest.pages >= 10000:
                raise FetchError("Data scan reached 10000-page safety budget; review scope")
            mutable = {"token"} if channel.kind == "stac" else {"STARTINDEX"}
            validate_page_url(harvest.start_url, harvest.next_url, mutable)
            request_hash = digest(harvest.next_url)
            if request_hash in harvest.visited_urls:
                raise FetchError("Data pagination loop")
            payload, media, summary = collect(channel, client, request_url=harvest.next_url)
            validate_page_url(harvest.next_url, summary["request_url"], set())
            fingerprint = summary["page_fingerprint"]
            if summary["raw_records"] and fingerprint in harvest.page_fingerprints:
                raise FetchError("Data service repeated a page; cursor not advanced")
            next_url = summary["next_url"]
            if next_url:
                validate_page_url(harvest.start_url, next_url, mutable)
                if digest(next_url) in [*harvest.visited_urls, request_hash]:
                    raise FetchError("Data next-page loop; cursor not advanced")
            summary.update(harvest_id=harvest.pk, page=harvest.pages + 1, partial=True)
            if (
                channel.kind == "stac"
                and not recent
                and summary["raw_records"] > 0
                and summary["records"] == 0
            ):
                return pause_retention(channel, harvest)
            with transaction.atomic():
                store_snapshot(channel, payload, media, summary, deduplicate=False)
                harvest.next_url = next_url
                harvest.pages += 1
                harvest.records += summary["records"]
                harvest.size_bytes += len(payload)
                harvest.visited_urls.append(request_hash)
                harvest.page_fingerprints.append(fingerprint)
                harvest.status = (
                    "bounded"
                    if recent and page_index == 1 and next_url
                    else "running"
                    if next_url
                    else "complete"
                )
                harvest.error = ""
                harvest.finished_at = None if harvest.status == "running" else timezone.now()
                harvest.save()
            if not next_url:
                break
    except Exception as exc:
        # Reload after a rolled-back transaction; keep last committed cursor.
        harvest.refresh_from_db()
        harvest.status = "failed"
        harvest.error = str(exc)[:300] if isinstance(exc, FetchError) else type(exc).__name__
        harvest.save(update_fields=["status", "error"])
        raise
    return {
        "channel": channel.slug,
        "status": "sampled" if recent else "collecting" if harvest.next_url else "complete",
        "harvest_id": harvest.pk,
        "pages": harvest.pages,
        "records": harvest.records,
        "bytes": harvest.size_bytes,
    }
