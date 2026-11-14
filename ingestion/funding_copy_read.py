"""Bounded local-copy boundary; never fetch or relabel calls as open today."""

import hashlib
import json
from datetime import datetime

from django.conf import settings
from django.db.models import BigIntegerField, F, Func

from ingestion import funding_calls as calls
from ingestion.data_retention import retained_snapshot
from ingestion.funding_search import (
    COMPETITIVE,
    DETAIL,
    IDENTIFIERS,
    IN_SCOPE,
    MAX_PASSES,
    PAGE_SIZE,
    PORTAL,
    PORTAL_COMPETITIVE,
    REFERENCE,
    SCOPE,
    source_dates,
)
from ingestion.funding_search_transport import PROFILES
from ingestion.transport import FetchError

FIELDS = {
    "identifier",
    "title",
    "source_url",
    "state",
    "next_deadline_raw",
    "deadline_dates_raw",
    "start_dates_raw",
    "source_status_raw",
    "source_type_raw",
}


def validate_profile(channel):
    if not isinstance(channel.config, dict):
        raise FetchError("Funding profile is not an object")
    key = calls.profile(channel)
    if (
        channel.registry.registry_id != calls.REGISTRY_ID
        or channel.slug != calls.slug(key)
        or not channel.access_approved
    ):
        raise FetchError("Funding profile or access not approved")
    return key


def count(value, limit):
    if type(value) is not int or not 0 <= value <= limit:
        raise FetchError("Funding count outside budget")
    return value


def checked_data(data, summary, key, fetched_at):
    pins = {
        "schema": "eu-funding-calls-v1",
        "profile": key,
        "title": calls.TITLES[key],
        "scope": SCOPE[key],
        "source_url": calls.BASE,
        "portal_url": calls.PORTAL,
        "attribution": calls.ATTRIBUTION,
        "state_rule": calls.STATE_RULE,
    }
    if (
        not isinstance(data, dict)
        or not isinstance(summary, dict)
        or set(data)
        != set(pins) | {"source_total", "outside_scope_count", "state_counts", "records"}
        or any(data[k] != v for k, v in pins.items())
        or any(
            summary.get(k) != v
            for k, v in {
                "collector": "funding_calls",
                "kind": "funding_calls",
                "scope": "EU",
                "profile": key,
            }.items()
        )
        or summary.get("partial") is not False
    ):
        raise FetchError("Funding schema or provenance mismatch")
    total = count(data["source_total"], PROFILES[key]["pages"] * PAGE_SIZE)
    budget = total * MAX_PASSES
    outside = count(data["outside_scope_count"], budget)
    records = data["records"]
    if (
        not isinstance(records, list)
        or len(records) > budget
        or count(summary.get("records"), budget) != len(records)
        or count(summary.get("source_count"), budget) != total
        or count(summary.get("outside_scope"), budget) != outside
    ):
        raise FetchError("Funding record count mismatch")
    try:
        as_of = datetime.fromisoformat(summary["as_of"])
        if as_of.tzinfo is None or as_of > fetched_at:
            raise ValueError("Invalid receipt time")
    except (KeyError, ValueError, TypeError) as exc:
        raise FetchError("Funding as_of invalid") from exc
    completeness = summary.get("completeness")
    if "completeness" in summary:
        passes = count(summary.get("scan_passes"), MAX_PASSES)
        if not (
            (completeness == "single_page" and total <= PAGE_SIZE and passes == 1)
            or (completeness == "converged_passes" and total > PAGE_SIZE and passes >= 2)
        ):
            raise FetchError("Funding paging evidence invalid")
    elif "scan_passes" in summary:
        raise FetchError("Funding incomplete paging evidence")
    if summary.get("retention_basis", "local_copy_age") != "local_copy_age":
        raise FetchError("Funding retention basis invalid")
    if "duplicate_rows_dropped" in summary:
        count(summary["duplicate_rows_dropped"], budget)
    counts, previous = dict.fromkeys(calls.STATES, 0), None
    for row in records:
        if (
            not isinstance(row, dict)
            or set(row) not in (FIELDS, FIELDS | {"source_reference"})
            or any(
                not isinstance(row[k], str) or not row[k].strip() or len(row[k]) > 1500
                for k in (
                    "identifier",
                    "title",
                    "source_url",
                    "state",
                    "source_status_raw",
                    "source_type_raw",
                )
            )
            or not IDENTIFIERS[key].fullmatch(row["identifier"])
            or not IN_SCOPE[key].match(row["identifier"])
            or row["source_type_raw"] not in {"1", "2", "8"}
            or row["source_status_raw"] not in {"31094501", "31094502"}
            or row["state"] not in calls.STATES
        ):
            raise FetchError("Funding row outside reviewed schema")
        reference = row.get("source_reference", "")
        needs_reference = PROFILES[key]["tolerant"] and row["source_type_raw"] == "8"
        if (
            needs_reference
            and (not isinstance(reference, str) or not REFERENCE.fullmatch(reference))
        ) or (not needs_reference and "source_reference" in row):
            raise FetchError("Funding call reference invalid")
        identity = (row["identifier"], reference)
        if previous is not None and identity <= previous:
            raise FetchError("Funding duplicate identity or order")
        previous = identity
        url = row["source_url"]
        allowed = url == DETAIL + row["identifier"] + ".json"
        if PROFILES[key]["tolerant"]:
            allowed |= url == PORTAL + "topic-details/" + row["identifier"]
        if row["source_type_raw"] == "8":
            allowed |= bool(COMPETITIVE.fullmatch(url)) or bool(PORTAL_COMPETITIVE.fullmatch(url))
        if not allowed:
            raise FetchError("Funding row URL outside reviewed catalogue")
        dates = source_dates(row["deadline_dates_raw"])
        source_dates(row["start_dates_raw"])
        future = [
            raw for raw, date in zip(row["deadline_dates_raw"], dates, strict=True) if date > as_of
        ]
        expected = min(future, key=datetime.fromisoformat) if future else None
        if row["next_deadline_raw"] != expected or row["state"] != calls.call_state(row, as_of):
            raise FetchError("Funding stored temporal evidence mismatch")
        counts[row["state"]] += 1
    if (
        data["state_counts"] != counts
        or any(count(data["state_counts"].get(k), budget) != v for k, v in counts.items())
        or any(count(summary.get("state_" + k), budget) != v for k, v in counts.items())
    ):
        raise FetchError("Funding state counts mismatch")
    return data


def read_copy(channel, snapshot_id, now):
    key = validate_profile(channel)
    copies = (
        channel.snapshots.filter(
            expires_at__gt=now,
            fetched_at__lte=now,
            download_job__isnull=True,
        )
        .annotate(
            actual_bytes=Func(
                F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField()
            ),
        )
        .defer("payload")
        .order_by("-fetched_at", "-pk")
    )
    snapshot = copies.filter(pk=snapshot_id).first() if snapshot_id else copies.first()
    if snapshot is None:
        return None
    if (
        snapshot.media_type != "application/json"
        or not isinstance(snapshot.summary, dict)
        or snapshot.actual_bytes != snapshot.size_bytes
        or not 0 < snapshot.size_bytes <= min(5 * 1024 * 1024, settings.DATA_AUTO_MAX_BYTES)
    ):
        raise FetchError("Funding copy byte budget or media invalid")
    snapshot.channel = channel
    snapshot.payload = (
        copies.filter(
            pk=snapshot.pk,
            sha256=snapshot.sha256,
            size_bytes=snapshot.size_bytes,
            actual_bytes=snapshot.actual_bytes,
            summary=snapshot.summary,
            fetched_at=snapshot.fetched_at,
            expires_at=snapshot.expires_at,
            media_type=snapshot.media_type,
        )
        .values_list("payload", flat=True)
        .first()
    )
    if snapshot.payload is None:
        raise FetchError("Funding copy changed during read")
    retained = retained_snapshot(snapshot, now)
    if retained is None:
        return None
    raw, summary, expires = retained
    if hashlib.sha256(raw).hexdigest() != snapshot.sha256:
        raise FetchError("Funding SHA mismatch")
    try:
        data = json.loads(raw)
        canonical = json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise FetchError("Funding JSON invalid") from exc
    if raw != canonical:
        raise FetchError("Funding noncanonical JSON")
    return {
        "snapshot": snapshot,
        "summary": summary,
        "expires_at": expires,
        "data": checked_data(data, summary, key, snapshot.fetched_at),
    }
