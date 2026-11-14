"""Bounded read of one approved CRFOP attribute register; no network or writes."""

import hashlib
import json
import re

from django.conf import settings
from django.db.models import BigIntegerField, F, Func

from ingestion import crfop
from ingestion.data_retention import retained_snapshot
from ingestion.transport import FetchError


def validate_profile(channel):
    config = channel.config
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("register"), str)
        or type(config.get("part", 1)) is not int
    ):
        raise FetchError("CRFOP profile invalid")
    expected = crfop.profile(config["register"], config.get("part", 1))
    if (
        channel.registry.registry_id != crfop.REGISTRY_ID
        or not channel.access_approved
        or (channel.slug, channel.url, config, channel.region, channel.kind, channel.attribution)
        != (expected["slug"], expected["url"], expected["config"], "PL", "table", crfop.ATTRIBUTION)
    ):
        raise FetchError("CRFOP profile or approval changed")
    return crfop.REGISTERS[config["register"]]


def checked_cells(columns, values):
    if not isinstance(values, list) or len(values) != len(columns):
        raise FetchError("CRFOP row width invalid")
    for column, value in zip(columns, values, strict=True):
        # The byte budget was checked before decoding; original boundary text may exceed 32767.
        if not isinstance(value, str):
            raise FetchError("CRFOP cell type invalid")
        crfop.validate_value(column, value)


def checked_data(data, summary, channel, spec):
    pins = {
        "schema": "crfop-register-v1",
        "register": channel.config["register"],
        "part": channel.config.get("part", 1),
        "parts": spec["parts"],
        "as_of": crfop.AS_OF,
        "license": "CC0 1.0",
        "attribution": crfop.ATTRIBUTION,
        "resource_id": spec["resource"],
        "url": spec["link"],
        "sha256": spec["sha256"],
        "act_columns": crfop.ACT_COLUMNS,
        "convention_columns": crfop.CONVENTION_COLUMNS,
        "source_flags_note": "Flagi t/f (np. archiwalny) zachowane bez interpretacji.",
    }
    if (
        not isinstance(data, dict)
        or not isinstance(summary, dict)
        or set(data) != set(pins) | {"units", "forms_total_in_register", "form_columns", "forms"}
        or any(type(data[k]) is not type(v) or data[k] != v for k, v in pins.items())
    ):
        raise FetchError("CRFOP provenance or schema changed")
    columns, rows, total = data["form_columns"], data["forms"], data["forms_total_in_register"]
    if (
        not isinstance(columns, list)
        or not 5 <= len(columns) <= 60
        or any(
            not isinstance(c, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", c) for c in columns
        )
        or len(set(columns)) != len(columns)
        or columns[:5]
        != ["inspire_id", "typ_formy_nazwa", "archiwalny", "nazwa", "data_utworzenia"]
        or set(columns) & set(crfop.ACT_COLUMNS + crfop.CONVENTION_COLUMNS + ["fop_id"])
        or type(total) is not int
        or not 1 <= total <= 20000
        or not isinstance(rows, list)
    ):
        raise FetchError("CRFOP register shape invalid")
    part, parts = pins["part"], pins["parts"]
    expected_count = (part * total + parts - 1) // parts - ((part - 1) * total + parts - 1) // parts
    units = {
        c: {"unit": None, "unit_status": "UNCONFIRMED"} for c in columns if c in crfop.AREA_COLUMNS
    }
    if len(rows) != expected_count or data["units"] != units:
        raise FetchError("CRFOP part count or units invalid")
    previous, acts = 0, 0
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "values", "legal_acts", "conventions"}
            or type(row["id"]) is not int
            or not previous < row["id"] <= 999999999
        ):
            raise FetchError("CRFOP form identity or order invalid")
        previous = row["id"]
        checked_cells(columns, row["values"])
        if not row["values"][0].startswith("PL.ZIPOP.1393."):
            raise FetchError("CRFOP INSPIRE identity invalid")
        for field, headers in (
            ("legal_acts", crfop.ACT_COLUMNS),
            ("conventions", crfop.CONVENTION_COLUMNS),
        ):
            if not isinstance(row[field], list) or len(row[field]) > 20000:
                raise FetchError("CRFOP continuation budget invalid")
            for cells in row[field]:
                checked_cells(headers, cells)
        acts += len(row["legal_acts"])
    expected_summary = {
        "kind": "crfop",
        "collector": "crfop",
        "register": pins["register"],
        "part": part,
        "parts": parts,
        "as_of": crfop.AS_OF,
        "scope": "PL",
        "records": len(rows),
        "forms_total_in_register": total,
        "legal_acts": acts,
        "units_unconfirmed": sorted(units),
        "partial": parts > 1,
    }
    if any(
        type(summary.get(k)) is not type(v) or summary[k] != v for k, v in expected_summary.items()
    ):
        raise FetchError("CRFOP summary differs from copy")
    return data


def read_copy(channel, snapshot_id, now):
    spec = validate_profile(channel)
    copies = (
        channel.snapshots.filter(expires_at__gt=now, download_job__isnull=True)
        .annotate(
            actual_bytes=Func(
                F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField()
            ),
        )
        .defer("payload")
    )
    snapshot = copies.filter(pk=snapshot_id).first() if snapshot_id else copies.first()
    if snapshot is None:
        return None
    snapshot.channel = channel
    if (
        snapshot.media_type != "application/json"
        or snapshot.actual_bytes != snapshot.size_bytes
        or not 0 < snapshot.size_bytes <= settings.DATA_AUTO_MAX_BYTES
    ):
        raise FetchError("CRFOP copy byte budget or media invalid")
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
        raise FetchError("CRFOP copy changed during read")
    retained = retained_snapshot(snapshot, now)
    if retained is None:
        return None
    payload, summary, expires = retained
    if hashlib.sha256(payload).hexdigest() != snapshot.sha256:
        raise FetchError("CRFOP copy SHA mismatch")
    try:
        data = json.loads(payload)
        canonical = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise FetchError("CRFOP copy JSON invalid") from exc
    if canonical != payload:
        raise FetchError("CRFOP copy encoding changed")
    return {
        "snapshot": snapshot,
        "summary": summary,
        "expires_at": expires,
        "data": checked_data(data, summary, channel, spec),
    }
