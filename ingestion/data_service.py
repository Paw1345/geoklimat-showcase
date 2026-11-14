"""Bounded GET-only data cache, independent of news."""

import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from ingestion.collection_evidence import begin_attempt, finish_attempt
from ingestion.models import DataChannel, DataSnapshot
from ingestion.observations import (
    filter_observations,
    observation_summary,
    retention_window,
    timestamp,
)
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError
from ingestion.worker_stop import requested as stop_requested


def query_url(url, **params):
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query))
    query.update(params)
    return urlunsplit(parsed._replace(query=urlencode(query)))


def collect(channel, client, on_demand=False, request_url=None):
    now = timezone.now()
    recent = channel.config.get("mode") == "stac_recent"
    bounded = channel.config.get("mode") == "stac_bounded"
    if recent or bounded:
        if bounded:
            from ingestion.stac_bounded import validate_page, validate_profile, validate_request
        else:
            from ingestion.stac_recent import validate_page, validate_profile, validate_request

        validate_profile(channel)
        if on_demand:
            raise FetchError("STAC recent is not a file download")
    if channel.region not in {"PL", "EU"}:
        raise FetchError("Only reviewed PL/EU data profiles allowed")
    if channel.config.get("mode") in {"pig_midas_scan", "pig_spatial_scan"}:
        from ingestion.pig_paging import probe

        if request_url or on_demand:
            raise FetchError("MIDAS scan probe does not support custom URLs or file mode")
        return probe(channel, client)
    if channel.config.get("mode") == "bdl_features_scan":
        from ingestion.bdl_paging import probe as probe_bdl

        if request_url or on_demand:
            raise FetchError("BDL scan probe does not support custom URLs or file mode")
        return probe_bdl(channel, client)
    if channel.config.get("mode") == "pig_midas_sample":
        from ingestion.pig_data import collect_sample

        if request_url or on_demand:
            raise FetchError("MIDAS sample does not support custom URLs or file mode")
        return collect_sample(channel, client)
    if channel.config.get("mode") == "crfop":
        from ingestion.crfop import collect as collect_crfop

        if request_url or on_demand:
            raise FetchError("CRFOP registers do not support custom URLs or file mode")
        return collect_crfop(channel, client)
    if channel.config.get("mode") == "pig_mwp_table":
        from ingestion.pig_mwp_tables import collect as collect_mwp

        if request_url or on_demand:
            raise FetchError("MWP tables do not support custom URLs or file mode")
        return collect_mwp(channel, client)
    if channel.config.get("mode") == "edo_cdi_raster":
        from ingestion.edo_cdi_raster import collect as collect_cdi

        if request_url or on_demand:
            raise FetchError("EDO CDI raster does not support custom URLs or file mode")
        return collect_cdi(channel, client, now.date())
    if channel.config.get("mode") == "effis_fwi_raster":
        from ingestion.effis_fwi_raster import collect as collect_fwi

        if request_url or on_demand:
            raise FetchError("EFFIS FWI raster does not support custom URLs or file mode")
        return collect_fwi(channel, client, now.date())
    if channel.config.get("mode") == "imgw_warnings":
        from ingestion.imgw_warnings import collect as collect_warnings

        if request_url or on_demand:
            raise FetchError("IMGW warnings do not support custom URLs or file mode")
        return collect_warnings(channel, client)
    if channel.config.get("mode") == "funding_calls":
        from ingestion.funding_calls import collect as collect_funding

        if request_url or on_demand:
            raise FetchError("Funding calls do not support custom URLs or file mode")
        return collect_funding(channel, client)
    if channel.config.get("mode") == "gios_tables":
        from ingestion.gios_tables import collect as collect_tables

        if request_url or on_demand:
            raise FetchError("GIOS result tables do not support custom URLs or file mode")
        return collect_tables(channel, client)
    if channel.config.get("mode") == "gios_prtr_releases":
        from ingestion.gios_prtr_table import collect as collect_prtr

        if request_url or on_demand:
            raise FetchError("PRTR annual table does not support custom URLs or file mode")
        return collect_prtr(channel, client)
    if channel.config.get("mode") == "gios_major_accidents":
        from ingestion.gios_major_accidents import collect as collect_accidents

        if request_url or on_demand:
            raise FetchError("GIOS register tables do not support custom URLs or file mode")
        return collect_accidents(channel, client)
    if channel.kind == "imgw_daily":
        from ingestion.imgw_daily import collect_daily

        if request_url or on_demand:
            raise FetchError("Daily IMGW archives do not support custom URLs or file mode")
        return collect_daily(channel, client, now)
    if channel.kind == "file":
        from ingestion.downloads import reviewed_checksum

        if not on_demand:
            raise FetchError("Files require an explicit on-demand request")
        if reviewed_checksum(channel):
            raise FetchError("Pinned files require the verified download queue")
        client.max_bytes = settings.DATA_MANUAL_MAX_BYTES
        response = client.get(channel.url)
        media = response.headers.get("content-type", "application/octet-stream").split(";")[0]
        return response.body, media, {"kind": "file", "scope": channel.region}
    url = request_url or channel.url
    if channel.kind == "stac" and not request_url:
        bbox = channel.config.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise FetchError("STAC requires an explicitly reviewed PL/EU bbox")
        collection = channel.config.get("collection")
        if not isinstance(collection, str) or not collection:
            raise FetchError("Select a reviewed STAC collection")
        url = query_url(
            url,
            bbox=",".join(map(str, bbox)),
            limit=10,
            sortby="-datetime",
            collections=collection,
            datetime=(
                now - (timedelta(hours=24) if recent or bounded else timedelta(days=30))
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            + "/"
            + now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    if recent or bounded:
        validate_request(channel, url)
    response = client.get(url)
    if recent or bounded:
        from ingestion.stac_integrity import invalid_constant, unique_object

        data = json.loads(
            response.body, object_pairs_hook=unique_object, parse_constant=invalid_constant
        )
    else:
        data = json.loads(response.body)
    summary = {"kind": channel.kind, "scope": channel.region}
    stamps = []
    zone = channel.config.get("timezone", "UTC")
    if channel.kind in {"imgw", "gios"}:
        filtered, stamps = filter_observations(data, channel.kind, now, zone)
        rows = filtered if channel.kind == "imgw" else filtered["Lista danych pomiarowych"]
        original = data if channel.kind == "imgw" else data["Lista danych pomiarowych"]
        if original and not rows:
            raise FetchError("No recent timestamped measurements; inspect schema/freshness")
        data = filtered
        summary["records"] = len(rows)
        if channel.kind == "gios":
            summary["quality"] = "Dane bieżące niezweryfikowane; nie są alertem zdrowotnym."
    elif channel.kind in {"geojson", "stac"}:
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise FetchError("Expected GeoJSON FeatureCollection")
        features = data.get("features")
        if not isinstance(features, list):
            raise FetchError("Missing features")
        if channel.config.get("pagination"):
            from ingestion.data_paging import page_metadata

            summary.update(page_metadata(channel, response.url, data))
        summary["partial"] = any(link.get("rel") == "next" for link in data.get("links", []))
        matched = data.get("numberMatched")
        summary["partial"] |= isinstance(matched, int) and matched > len(features)
        if channel.kind == "stac":
            data, stamps = filter_observations(data, "stac", now)
            features = data["features"]
            summary["assets_downloaded"] = False
        elif len(features) >= int(channel.config.get("feature_limit", 1000)):
            summary["partial"] = True
        summary["records"] = len(features)
        summary["bbox"] = channel.config.get("bbox")
    else:
        raise FetchError("Unsupported data profile")
    if stamps:
        summary = observation_summary(summary, stamps, zone)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if recent or bounded:
        validate_page(channel, payload, summary)
        summary.update(
            acquisition_mode="bounded_window" if bounded else "recent_sample",
            search_window_hours=24,
            sample_page_limit=None if bounded else 2,
        )
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("Automatic data payload exceeds budget")
    return payload, "application/json", summary


def store_snapshot(channel, payload, media, summary, deduplicate=True):
    now = timezone.now()
    digest = hashlib.sha256(payload).hexdigest()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(726043)")
        latest = (
            channel.snapshots.filter(expires_at__gt=now, download_job__isnull=True)
            .defer("payload")
            .first()
        )
        # A collector may name a content key when the provider varies bytes that carry
        # no data (EDO CDI GeoTIFF sometimes omits GeoKeys, 2026-10-14): same key, no copy.
        content_key = summary.get("content_sha256")
        if (
            deduplicate
            and latest
            and (
                latest.sha256 == digest
                or (content_key and (latest.summary or {}).get("content_sha256") == content_key)
            )
        ):
            return latest, False
        used = DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
        if used + len(payload) > settings.DATA_STORAGE_MAX_BYTES:
            raise FetchError("Data quota reached; prune expired data or reduce scope")
        expires = now + retention_window()
        # Versioned payloads are filtered per observation on maintenance AND every export.
        # Legacy summaries retain the conservative whole-page deadline until upgraded.
        expiry_stamp = summary.get("newest_observation") or summary.get("oldest_observation")
        if expiry_stamp:
            expires = min(expires, timestamp(expiry_stamp) + retention_window())
        snapshot = DataSnapshot.objects.create(
            channel=channel,
            payload=payload,
            size_bytes=len(payload),
            sha256=digest,
            media_type=media[:100],
            summary=summary,
            expires_at=expires,
        )
    return snapshot, True


TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


def transient_scan_retry(channel, exc):
    """A provider hiccup on a long paged scan resumes from the kept cursor within the
    hour instead of waiting the whole interval (CBDG 30 days, 2026-10-08)."""
    if not channel.config.get("pagination") or not isinstance(exc, FetchError):
        return None
    if exc.status not in TRANSIENT_STATUSES and str(exc) != "Network connection or timeout failure":
        return None
    if not channel.harvests.filter(status="failed").exclude(next_url="").exists():
        return None
    return timedelta(seconds=min(max(3600, exc.retry_after or 0), 6 * 3600))


def run_data(slug=None, dry_run=False, force=False, on_demand=False, *, manual=False):
    channels = DataChannel.objects.all().order_by("pk")
    if slug:
        channels = channels.filter(slug=slug)
    elif on_demand:
        raise FetchError("On-demand requires one channel")
    if not dry_run:
        channels = channels.filter(access_approved=True)
        if not on_demand:
            channels = channels.filter(enabled=True).exclude(kind="file")
    if not force and not dry_run and not on_demand:
        channels = channels.filter(next_fetch_at__lte=timezone.now())
    results = []
    with batch_lock() as acquired:
        if not acquired:
            return [{"status": "busy"}]
        client = Client()
        for channel in channels:
            if stop_requested():
                break
            attempt = begin_attempt(channel, dry_run=dry_run, manual=manual)
            retry = None
            try:
                client.max_bytes = settings.DATA_AUTO_MAX_BYTES
                if channel.config.get("mode") == "bdl_rdlp_manual_scan":
                    raise FetchError("Use bdl_scan; this profile must remain manual and disabled")
                if channel.config.get("mode") == "bdl_raw_sample":
                    from ingestion.bdl_samples import run_sample

                    if on_demand:
                        raise FetchError("BDL samples are not manual file downloads")
                    results.append(run_sample(channel, client, dry_run))
                    continue
                if channel.config.get("mode") == "gios_prtr_releases":
                    from ingestion.gios_prtr_table import run_profile

                    if on_demand:
                        raise FetchError("PRTR annual table is not a manual file download")
                    results.append(run_profile(channel, client, dry_run))
                    continue
                if channel.config.get("pagination") and not dry_run:
                    from ingestion.data_paging import advance_harvest

                    result = advance_harvest(channel, client)
                    channel.last_status = result["status"]
                    channel.last_error = result.get("error", "")
                    channel.checked_at = timezone.now()
                    channel.next_fetch_at = timezone.now() + (
                        timedelta(seconds=30)
                        if result["status"] == "collecting"
                        else timedelta(minutes=max(15, channel.interval_minutes))
                    )
                    channel.save(
                        update_fields=["last_status", "last_error", "checked_at", "next_fetch_at"]
                    )
                    results.append(result)
                    continue
                payload, media, summary = collect(channel, client, on_demand)
                status = "dry_run"
                if not dry_run:
                    _, created = store_snapshot(channel, payload, media, summary)
                    status = "stored" if created else "unchanged"
                result = {
                    "channel": channel.slug,
                    "status": status,
                    "bytes": len(payload),
                    **summary,
                }
                error = ""
            except Exception as exc:
                error = str(exc)[:300] if isinstance(exc, FetchError) else type(exc).__name__
                result = {"channel": channel.slug, "status": "failed", "error": error}
                retry = transient_scan_retry(channel, exc)
            if not dry_run:
                if channel.config.get("mode") == "gios_prtr_releases":
                    from ingestion.gios_prtr_table import mark_failure

                    mark_failure(channel, error)
                    results.append(result)
                    continue
                channel.last_status, channel.last_error = result["status"], error
                channel.checked_at = timezone.now()
                channel.next_fetch_at = timezone.now() + (
                    retry or timedelta(minutes=max(15, channel.interval_minutes))
                )
                if channel.config.get("mode") == "bdl_raw_sample":
                    # Never overwrite an approval/profile changed during a failed GET.
                    DataChannel.objects.filter(
                        pk=channel.pk,
                        config=channel.config,
                        url=channel.url,
                        enabled=channel.enabled,
                        access_approved=channel.access_approved,
                        interval_minutes=channel.interval_minutes,
                    ).update(
                        last_status=channel.last_status,
                        last_error=channel.last_error,
                        checked_at=channel.checked_at,
                        next_fetch_at=channel.next_fetch_at,
                    )
                else:
                    channel.save(
                        update_fields=["last_status", "last_error", "checked_at", "next_fetch_at"]
                    )
            if attempt is not None:
                result["evidence_status"] = finish_attempt(attempt, result["status"])
            results.append(result)
    return results
