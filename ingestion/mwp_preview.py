"""One bounded MWP copy boundary shared by preview and pinned search."""

import hashlib
import json
from datetime import date
from itertools import islice

from django.conf import settings
from django.db.models import BigIntegerField, F, Func

from ingestion import pig_mwp_tables as mwp
from ingestion.data_retention import retained_snapshot
from ingestion.transport import FetchError


def owns_preview(channel, snapshot):
    """Dispatch clues are not approval: changed MWP profiles must not use raw fallback."""
    return (
        (isinstance(channel.config, dict) and channel.config.get("mode") == "pig_mwp_table")
        or channel.slug in {mwp.slug(key) for key in mwp.TABLES}
        or (
            snapshot is not None
            and isinstance(snapshot.summary, dict)
            and snapshot.summary.get("collector") == "pig_mwp_table"
        )
    )


def checked_data(data, summary, channel, key, spec):
    pins = {
        "schema": "pig-mwp-table-v1",
        "table": key,
        "title": spec["title"],
        "source_url": channel.url,
        "evidence_url": mwp.EVIDENCE,
        "terms_url": mwp.TERMS,
        "attribution": mwp.ATTRIBUTION,
        "scope_note": spec["note"],
        "station_link": "OTWOR_ID = ID w warstwie punktów MWP (pig-mwp-scan)",
        "units": spec["units"],
        "source_generated_at": None,
        "columns": list(spec["fields"]),
        "dictionary_columns": spec["dictionary"],
    }
    if (
        not isinstance(data, dict)
        or not isinstance(summary, dict)
        or set(data) != set(pins) | {"dictionaries", "rows", "period"}
        or any(type(data[k]) is not type(v) or data[k] != v for k, v in pins.items())
    ):
        raise FetchError("MWP provenance or schema changed")
    dictionaries, rows = data["dictionaries"], data["rows"]
    if (
        not isinstance(dictionaries, dict)
        or set(dictionaries) != set(spec["dictionary"])
        or not isinstance(rows, list)
        or not 1 <= len(rows) <= spec["max_rows"]
    ):
        raise FetchError("MWP table or dictionaries invalid")
    for items in dictionaries.values():
        if (
            not isinstance(items, list)
            or len(items) > len(rows)
            or any(not isinstance(item, str) for item in items)
            or len(set(items)) != len(items)
        ):
            raise FetchError("MWP dictionary values invalid")
    stations, dates = set(), set()
    previous = -1
    for row in rows:
        if not isinstance(row, list) or len(row) != len(spec["fields"]):
            raise FetchError("MWP row width invalid")
        decoded = {}
        for (name, kind), item in zip(spec["fields"].items(), row, strict=True):
            if name in dictionaries and item is not None:
                items = dictionaries[name]
                if type(item) is not int or not 0 <= item < len(items):
                    raise FetchError("MWP dictionary index invalid")
                item = items[item]
            try:
                checked = mwp.value(name, kind, item, spec["date"])
            except OverflowError as exc:
                raise FetchError("MWP numeric value outside finite range") from exc
            if checked != item:
                raise FetchError("MWP date encoding changed")
            if name == spec["date"]:
                try:
                    date.fromisoformat(item)
                except ValueError as exc:
                    raise FetchError("MWP source date outside calendar") from exc
            decoded[name] = item
        identity = decoded["OBJECTID"]
        if not previous < identity <= 2**53:
            raise FetchError("MWP identity or order invalid")
        previous = identity
        stations.add(decoded["OTWOR_ID"])
        dates.add(decoded[spec["date"]])
    period = [min(dates), max(dates)]
    expected = {
        "kind": "pig_mwp_table",
        "collector": "pig_mwp_table",
        "table": key,
        "scope": "PL",
        "records": len(rows),
        "source_count": len(rows),
        "stations": len(stations),
        "period_start": period[0],
        "period_end": period[1],
        "partial": False,
        "retention_basis": "local_copy_age",
    }
    if data["period"] != period or any(
        type(summary.get(k)) is not type(v) or summary[k] != v for k, v in expected.items()
    ):
        raise FetchError("MWP period or summary differs from copy")
    return data


def decoded_row(data, index):
    """Decode an already validated row without normalizing source values."""
    dictionaries = data["dictionaries"]
    return {
        name: dictionaries[name][item] if name in dictionaries and item is not None else item
        for name, item in zip(data["columns"], data["rows"][index], strict=True)
    }


def validate_profile(channel):
    config = channel.config
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("table"), str)
        or type(config.get("layer")) is not int
    ):
        raise FetchError("MWP profile invalid")
    key, spec = mwp.profile(channel)
    if (
        not channel.access_approved
        or channel.registry.registry_id != mwp.REGISTRY_ID
        or channel.slug != mwp.slug(key)
        or channel.attribution != mwp.ATTRIBUTION
    ):
        raise FetchError("MWP profile or approval changed")
    return key, spec


def read_copy(channel, snapshot_id, now):
    key, spec = validate_profile(channel)
    copies = (
        channel.snapshots.filter(expires_at__gt=now, download_job__isnull=True)
        .annotate(
            actual_bytes=Func(F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField())
        )
        .defer("payload")
    )
    snapshot = copies.filter(pk=snapshot_id).first() if snapshot_id else copies.first()
    if snapshot is None:
        return None
    if (
        snapshot.media_type != "application/json"
        or not isinstance(snapshot.summary, dict)
        or snapshot.actual_bytes != snapshot.size_bytes
        or not 0 < snapshot.size_bytes <= min(settings.DATA_AUTO_MAX_BYTES, 5 * 1024 * 1024)
    ):
        raise FetchError("MWP copy byte budget or media invalid")
    # Repeat the byte/metadata fence before materializing a possibly changed BLOB.
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
        raise FetchError("MWP copy changed during read")
    snapshot.channel = channel
    retained = retained_snapshot(snapshot, now)
    if retained is None:
        return None
    payload, summary, expires = retained
    if hashlib.sha256(payload).hexdigest() != snapshot.sha256:
        raise FetchError("MWP copy SHA mismatch")
    try:
        data = json.loads(payload)
        canonical = json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise FetchError("MWP copy JSON invalid") from exc
    if canonical != payload:
        raise FetchError("MWP copy encoding changed")
    return {
        "snapshot": snapshot,
        "summary": summary,
        "expires_at": expires,
        "data": checked_data(data, summary, channel, key, spec),
    }


def read_preview(channel, now):
    result = read_copy(channel, None, now)
    if result is None:
        return None
    data = result["data"]
    preview = [decoded_row(data, i) for i in islice(range(len(data["rows"])), 3)]
    text = json.dumps(
        {"units": data["units"], "rows": preview}, ensure_ascii=False, allow_nan=False, indent=2
    )
    if len(text) > 12000:
        raise FetchError("MWP preview output exceeds budget")
    return {"snapshot": result["snapshot"], "text": text}
