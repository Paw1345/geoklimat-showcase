"""Bounded, read-only receipt and rows from one approved PRTR v1 copy."""

import hashlib
import json

from django.conf import settings
from django.db.models import BigIntegerField, F, Func

from ingestion.data_retention import retained_snapshot
from ingestion.gios_prtr_table import ATTRIBUTION, CONFIG, URL, original, validate_profile
from ingestion.prtr_releases import MAX_RELEASES, NUMBER
from ingestion.transport import FetchError

TEXT_LIMITS = {
    "facility_id": 100,
    "facility_name": 500,
    "medium_code": 5,
    "pollutant_code": 100,
    "method_code": 1,
    "total_quantity": 64,
    "accidental_quantity": 64,
    "unit_code": 3,
    "unit": 7,
    "reporting_year_source": 13,
}
NULLABLE = {"facility_name", "total_quantity", "accidental_quantity"}


def checked_rows(data, summary):
    records = data.get("records")
    if not isinstance(records, list) or len(records) > MAX_RELEASES:
        raise FetchError("PRTR record budget or shape invalid")
    if type(summary.get("records")) is not int or summary["records"] != len(records):
        raise FetchError("PRTR record count mismatch")
    for row in records:
        if not isinstance(row, dict) or set(row) != set(TEXT_LIMITS) | {"reporting_year"}:
            raise FetchError("PRTR row fields not reviewed")
        for field, limit in TEXT_LIMITS.items():
            value = row[field]
            if value is None and field in NULLABLE:
                continue
            if not isinstance(value, str) or not value or len(value) > limit:
                raise FetchError("PRTR row value type or length invalid")
        if (
            row["reporting_year"] != 2017
            or row["reporting_year_source"] != "report_header"
            or row["unit"] != "kg/year"
            or row["unit_code"] != "KGM"
            or row["medium_code"] not in {"AIR", "WATER", "LAND"}
            or row["method_code"] not in {"M", "C", "E"}
        ):
            raise FetchError("PRTR row scope or units invalid")
        for field in ("total_quantity", "accidental_quantity"):
            if row[field] is not None and not NUMBER.fullmatch(row[field]):
                raise FetchError("PRTR quantity is not original decimal text")
    return records


def read_copy(channel, snapshot_id, now):
    validate_profile(channel)
    if not channel.access_approved:
        raise FetchError("PRTR access not approved")
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
        raise FetchError("PRTR copy byte budget or media invalid")
    # Recheck byte size in the same SELECT that loads the BLOB, rather than trusting metadata.
    snapshot.payload = (
        copies.filter(
            pk=snapshot.pk,
            sha256=snapshot.sha256,
            size_bytes=snapshot.size_bytes,
            actual_bytes=snapshot.actual_bytes,
        )
        .values_list("payload", flat=True)
        .first()
    )
    if snapshot.payload is None:
        raise FetchError("PRTR copy changed during read")
    retained = retained_snapshot(snapshot, now)
    if retained is None:
        return None
    payload, summary, expires = retained
    if hashlib.sha256(payload).hexdigest() != snapshot.sha256:
        raise FetchError("PRTR copy SHA mismatch")
    try:
        data = json.loads(payload)
        # v1 is emitted canonically: this also refuses duplicate keys and non-finite values.
        canonical = json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise FetchError("PRTR copy JSON invalid") from exc
    pins = {
        "schema": CONFIG["schema"],
        "source_url": URL,
        "source_sha256": original.SHA256,
        "source_bytes": original.SIZE,
        "reporting_year": 2017,
        "reporting_year_source": "report_header",
        "scope": "PL",
        "unit": "kg/year",
        "product_scope": "PL2017_pollutant_releases_only",
        "collector": "gios_prtr_releases",
        "kind": "gios_prtr_releases",
    }
    if (
        not isinstance(data, dict)
        or canonical != payload
        or data.get("provenance") != summary
        or data.get("attribution") != ATTRIBUTION
        or any(summary.get(k) != v for k, v in pins.items())
    ):
        raise FetchError("PRTR copy provenance or version mismatch")
    return {
        "snapshot": snapshot,
        "summary": summary,
        "expires_at": expires,
        "records": checked_rows(data, summary),
    }
