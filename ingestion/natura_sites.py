"""Bounded inspection of the reviewed Natura 2000 sites CSV; never fetches or writes."""

import csv
import io
import re
import tempfile
import threading
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import connection
from django.db.models import BigIntegerField, F, Func

from ingestion.downloads import CHUNK_BYTES
from ingestion.eea_natura_file import CONFIG, SIZE, SLUG, URL
from ingestion.file_exports import write_verified_file
from ingestion.transport import FetchError

SHA256 = "65aac88aae9f02db977a69bbab460d2bec6c92434b1bb4eb2ddec05e17b5561e"
MAX_BYTES = 64 * 1024 * 1024
MAX_FIELD_CHARS = 1024 * 1024
MAX_ROWS = 100000
CSV_LOCK = threading.RLock()
COUNTRIES = frozenset(
    "AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK".split()
)
FIELDS = (
    "COUNTRY_CODE SITECODE SITENAME SITETYPE DATE_COMPILATION DATE_UPDATE DATE_SPA "
    "SPA_LEGAL_REFERENCE DATE_PROP_SCI DATE_CONF_SCI DATE_SAC SAC_LEGAL_REFERENCE "
    "EXPLANATIONS AREAHA LENGTHKM MARINE_AREA_PERCENTAGE DOCUMENTATION QUALITY "
    "DESIGNATION OTHERCHARACT LATITUDE LONGITUDE INSPIRE_ID"
).split()


def inspect_value(field, raw, dates, warn):
    if not raw:
        return
    if field.startswith("DATE_"):
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                raise ValueError
            parsed = date.fromisoformat(raw)
        except ValueError:
            warn("invalid_date")
            return
        span = dates.setdefault(
            field, {"min_raw": raw, "max_raw": raw, "count": 0, "after_release": 0}
        )
        span["min_raw"], span["max_raw"] = min(span["min_raw"], raw), max(span["max_raw"], raw)
        span["count"] += 1
        if parsed > date(2021, 12, 31):
            span["after_release"] += 1
            warn("after_release")
        if parsed.year < 1950:
            warn("early_date_unconfirmed")
    elif field in {"LATITUDE", "LONGITUDE", "AREAHA", "LENGTHKM", "MARINE_AREA_PERCENTAGE"}:
        try:
            number = Decimal(raw)
            if not number.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            warn("invalid_number")
            return
        if field in {"LATITUDE", "LONGITUDE"}:
            bound = 90 if field == "LATITUDE" else 180
            if not -bound <= number <= bound:
                warn("out_of_range")
            if number == 0:
                warn("zero")
        elif field == "AREAHA" and number == -99:
            warn("unknown_minus99")
        elif number < 0 or (field == "MARINE_AREA_PERCENTAGE" and number > 100):
            warn("out_of_range")


def parse_sites(stream):
    """Inspect a bounded seekable binary stream to EOF before returning any result."""
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    if not 0 < size <= MAX_BYTES:
        raise FetchError("Natura CSV outside byte budget")
    stream.seek(0)
    countries, raw_countries, types, blanks, quality = (Counter() for _ in range(5))
    seen, dates, examples = set(), {}, []
    records, max_field = 0, 0
    with CSV_LOCK:
        previous_limit = csv.field_size_limit(MAX_FIELD_CHARS)
        text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        try:
            reader = csv.reader(text, delimiter=",", strict=True)
            if next(reader, None) != FIELDS:
                raise FetchError("Natura CSV schema changed")
            for row in reader:
                records += 1
                if records > MAX_ROWS or len(row) != len(FIELDS) or any("\x00" in v for v in row):
                    raise FetchError("Natura CSV row shape or budget invalid")
                values = dict(zip(FIELDS, row, strict=True))
                country, identity = values["COUNTRY_CODE"], values["SITECODE"]
                if (
                    country.upper() not in COUNTRIES
                    or not re.fullmatch(r"[A-Z]{2}[A-Za-z0-9]{1,62}", identity)
                    or identity[:2] != country.upper()
                    or identity in seen
                ):
                    raise FetchError("Natura CSV country or unique identity invalid")
                seen.add(identity)
                countries[country.upper()] += 1
                raw_countries[country] += 1
                types[values["SITETYPE"]] += 1
                if country != country.upper():
                    quality["COUNTRY_CODE_noncanonical_case"] += 1
                for field, raw in values.items():
                    max_field = max(max_field, len(raw))
                    if not raw:
                        blanks[field] += 1

                    def warn(reason, field=field, raw=raw, identity=identity):
                        quality[field + "_" + reason] += 1
                        if len(examples) < 10:
                            example = {"sitecode": identity, "field": field, "raw": raw[:200]}
                            if len(raw) > 200:
                                example["raw_truncated"] = True
                            examples.append(example)

                    inspect_value(field, raw, dates, warn)
            if records == 0:
                raise FetchError("Natura CSV contains no sites")
        except (csv.Error, UnicodeError) as error:
            raise FetchError("Natura CSV text or quoting invalid") from error
        finally:
            text.detach()
            csv.field_size_limit(previous_limit)
    return {
        "records": records,
        "unique_sitecodes": len(seen),
        "fields": FIELDS.copy(),
        "country_counts": dict(sorted(countries.items())),
        "country_counts_raw": dict(sorted(raw_countries.items())),
        "poland_records": countries["PL"],
        "type_counts_raw": dict(sorted(types.items())),
        "blank_counts": dict(sorted(blanks.items())),
        "max_field_chars": max_field,
        "date_ranges": dates,
        "quality_counts": dict(sorted(quality.items())),
        "quality_examples": examples,
        "quality_status": "warn" if quality or blanks else "pass",
        "structure_verified": True,
        "release": CONFIG["release"],
        "current_registry_confirmed": False,
        "national_completeness_confirmed": False,
        "geometry_included": False,
        "history_30_days_applicable": False,
        "coordinate_reference": "ETRS89 site centres; provider feature catalogue",
        "date_precision": "source year-month semantics; raw CSV dates retained",
    }


def read_verified_sites(job, now):
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise FetchError("Natura reader requires a PostgreSQL read-only snapshot")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only')"
        )
        if cursor.fetchone() != ("repeatable read", "on"):
            raise FetchError("Natura reader requires REPEATABLE READ READ ONLY")
    channel = job.channel
    if (
        channel.slug != SLUG
        or channel.url != URL
        or channel.config != CONFIG
        or channel.kind != "file"
        or channel.region != "EU"
        or not channel.access_approved
        or job.total_bytes != SIZE
        or job.sha256 != SHA256
        or not isinstance(channel.attribution, str)
        or not 0 < len(channel.attribution) <= 2000
    ):
        raise FetchError("Natura copy is not the reviewed original")
    expected_chunks = (SIZE + CHUNK_BYTES - 1) // CHUNK_BYTES
    lengths = list(
        job.chunks.annotate(
            actual_bytes=Func(F("payload"), function="OCTET_LENGTH", output_field=BigIntegerField())
        ).values_list("size_bytes", "actual_bytes")[: expected_chunks + 1]
    )
    if (
        not 0 < SIZE <= MAX_BYTES
        or len(lengths) != expected_chunks
        or any(not 0 < declared == actual <= CHUNK_BYTES for declared, actual in lengths)
        or sum(actual for _, actual in lengths) != SIZE
    ):
        raise FetchError("Natura actual payload outside reviewed byte budget")
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as stream:
        digest = write_verified_file(job, now, stream)
        summary = parse_sites(stream)
    summary.update(
        job_id=job.pk,
        channel_id=channel.pk,
        source_sha256=digest,
        source_url=URL,
        source_bytes=SIZE,
        attribution=channel.attribution,
        license="CC BY 4.0 (DG ENV)",
        dataset_uuid=CONFIG["dataset_uuid"],
        copied_at=job.created_at.isoformat(),
        expires_at=job.expires_at.isoformat(),
        retention_basis="local_copy_age",
    )
    return summary
