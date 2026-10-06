"""Disposable bounded GET diagnosis, not a stored or resumable STAC harvest."""

import json
import time

from django.conf import settings

from ingestion.data_paging import initial_url, validate_page_url
from ingestion.data_service import collect
from ingestion.stac_recent import validate_profile, validate_request
from ingestion.transport import FetchError

MIB = 1024**2


def plan(channel, pages=6, budget_mib=8, seconds=60):
    if (
        type(pages) is not int
        or not 1 <= pages <= 10
        or type(budget_mib) is not int
        or not 1 <= budget_mib <= 16
        or type(seconds) is not int
        or not 1 <= seconds <= 90
        or not channel.enabled
        or not channel.access_approved
    ):
        raise ValueError("Invalid STAC probe limits or inactive channel")
    validate_profile(channel)
    return {
        "channel_id": channel.pk,
        "region": channel.region,
        "window_hours": 24,
        "max_pages": pages,
        "max_records": pages * 10,
        "budget_bytes": budget_mib * MIB,
        "request_start_budget_seconds": seconds,
        "network_requested": False,
        "stored": False,
        "activation_approved": False,
    }


class ResponseBudget:
    def __init__(self, client, maximum):
        self.client, self.maximum = client, maximum
        self.raw_bytes = 0
        self.remaining = maximum

    def get(self, url):
        self.client.max_bytes = min(settings.DATA_AUTO_MAX_BYTES, self.remaining)
        response = self.client.get(url)
        self.raw_bytes += len(response.body)
        if len(response.body) > self.client.max_bytes or self.raw_bytes > self.maximum:
            raise FetchError("STAC diagnostic response exceeds remaining budget")
        return response


def measure(channel, client, pages=6, budget_mib=8, seconds=60):
    result = plan(channel, pages, budget_mib, seconds)
    started = time.monotonic()
    start = initial_url(channel)
    begin, end = validate_request(channel, start)
    url = start
    guarded = ResponseBudget(client, result["budget_bytes"])
    normalized, accepted, records = 0, 0, 0
    seen, fingerprints, identities = set(), set(), set()
    reason, failed, ended = "page_budget", False, False
    for _ in range(pages):
        if time.monotonic() - started >= seconds:
            reason = "time_budget"
            break
        remaining = result["budget_bytes"] - max(normalized, guarded.raw_bytes)
        if remaining <= 0:
            reason = "byte_budget"
            break
        guarded.remaining = remaining
        try:
            validate_profile(channel)
            validate_page_url(start, url, {"token"})
            validate_request(channel, url)
            if url in seen:
                raise FetchError("STAC diagnostic page loop")
            seen.add(url)
            payload, _, summary = collect(channel, guarded, request_url=url)
            if time.monotonic() - started >= seconds:
                reason = "time_budget"
                break
            if len(payload) > remaining:
                reason = "byte_budget"
                break
            validate_page_url(url, summary["request_url"], set())
            next_url = summary["next_url"]
            if next_url:
                validate_page_url(start, next_url, {"token"})
                validate_request(channel, next_url)
                if next_url in seen:
                    raise FetchError("STAC diagnostic next-page loop")
            ids = {feature["id"] for feature in json.loads(payload)["features"]}
            if ids & identities or (
                summary["raw_records"] and summary["page_fingerprint"] in fingerprints
            ):
                raise FetchError("STAC diagnostic duplicate scenes or repeated page")
            identities.update(ids)
            fingerprints.add(summary["page_fingerprint"])
            normalized += len(payload)
            accepted += 1
            records += summary["records"]
            url = next_url
            if not url:
                ended, reason = True, "pagination_end"
                break
        except (FetchError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
            failed, reason = True, "fetch_or_validation_failed"
            break
    return {
        **result,
        "network_requested": True,
        "search_start": begin,
        "search_end": end,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "pages_attempted": len(seen),
        "pages_validated": accepted,
        "records_validated": records,
        "unique_ids_in_validated_pages": len(identities),
        "validated_metadata_bytes": normalized,
        "returned_response_body_bytes": guarded.raw_bytes,
        "all_network_bytes_measured": False,
        "stop_reason": reason,
        "failed": failed,
        "pagination_end_observed": ended,
        "remaining_pagination_known": accepted > 0 and bool(url) and not failed,
        "window_bytes_if_pagination_end": normalized if ended else None,
        "full_day_coverage_verified": False,
        "full_current_30_day_coverage_verified": False,
        "geographic_mask_verified": False,
        "provider_snapshot_consistency_verified": False,
    }
