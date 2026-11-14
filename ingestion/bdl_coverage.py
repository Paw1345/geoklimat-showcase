"""BDL adapter for the bounded shared coverage reader; no SQL or remote requests."""

import json

from ingestion.bdl_paging import page_summary, validate_profile
from ingestion.bdl_probe import invalid_constant, unique_object
from ingestion.data_paging import digest
from ingestion.transport import FetchError

POLICY = "bdl-null-reported-v1"
COUNTS = ("geometry_present_records", "geometry_missing_records", "geometry_missing_ids")


def geometry_metadata(rows, requested, full):
    present = missing = unknown = 0
    invalid = False
    for row in rows:
        s = row["summary"]
        records = s["records"]
        if not requested:
            continue
        if "geometry_policy" not in s:
            unknown += records
            continue
        p, m, ids = (s.get(key) for key in COUNTS)
        first = s.get("first_id")
        if (
            s["geometry_policy"] != POLICY
            or s.get("geometry_requested") is not True
            or type(p) is not int
            or type(m) is not int
            or p < 0
            or m < 0
            or p + m != records
            or not isinstance(ids, list)
            or len(ids) != m
            or type(first) is not int
            or any(type(i) is not int or not first <= i < first + records for i in ids)
            or ids != sorted(set(ids))
            or s.get("geometry_included") is not (m == 0)
        ):
            invalid = True
            unknown += records
            continue
        present += p
        missing += m
    complete = bool(requested and full and not unknown and not missing and not invalid)
    status = "unconfirmed"
    if not requested:
        status = "not_requested"
    elif missing:
        status = "warn"
    elif complete:
        status = "complete"
    return {
        "status": status,
        "present_records": present,
        "missing_records": missing,
        "unknown_records": unknown,
        "invalid_metadata": invalid,
        "full_geometry_coverage": complete,
        "scope": "available_copy_metadata",
    }


def verify_page(harvest, row, raw):
    channel = harvest.channel
    _, profile = validate_profile(channel)
    s = row["summary"]
    years = s.get("source_years")
    if not isinstance(years, list) or len(years) != 1 or type(years[0]) is not int:
        raise FetchError("BDL retained source year missing")
    pinned = {"count": s["source_count"], "source_year": years[0], "scope": harvest.config_hash}
    if (
        digest(
            [channel.url, channel.kind, channel.region, channel.config, pinned["count"], years[0]]
        )
        != harvest.config_hash
    ):
        raise FetchError("BDL retained scope mismatch")
    data = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if not isinstance(data, dict) or row["media_type"] != "application/geo+json":
        raise FetchError("BDL retained page schema mismatch")
    actual = page_summary(channel, data, (s["page"] - 1) * profile["limit"], pinned)
    keys = [
        "collector",
        "identity_scope_hash",
        "records",
        "source_count",
        "source_years",
        "first_id",
        "last_id",
        "geometry_included",
    ]
    if profile["geometry"] and ("geometry_policy" in s or actual["geometry_missing_records"]):
        keys += ["geometry_policy", "geometry_requested", *COUNTS]
    if any(s.get(key) != actual.get(key) for key in keys):
        raise FetchError("BDL retained page metadata mismatch")
    return data
