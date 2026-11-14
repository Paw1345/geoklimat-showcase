"""Staff metadata view and internal verified-report boundary; no CSV in the parent."""

import re
from collections import Counter
from datetime import date
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.db import DatabaseError, connection, transaction
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core import natura_runner
from core.natura_runner import ReportRefused
from ingestion import eea_natura_file as profile
from ingestion.downloads import CHUNK_BYTES, configuration
from ingestion.models import DataChannel, DataSnapshot, DownloadJob
from ingestion.natura_sites import COUNTRIES, FIELDS, MAX_FIELD_CHARS, MAX_ROWS, SHA256

MAX_ID = 2**63 - 1
COPY_FIELDS = ("id", "created_at", "expires_at", "total_bytes", "sha256")
SOURCE_URL = (
    "https://sdi.eea.europa.eu/catalogue/srv/eng/catalog.search#/metadata/"
    "dae737fd-7ee1-4b0a-9eb7-1954eec00c65"
)
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"


def _nosniff(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        response = view(*args, **kwargs)
        response["X-Content-Type-Options"] = "nosniff"
        return response

    return wrapped


def _job_id(query):
    if set(query) - {"job"} or any(len(query.getlist(key)) != 1 for key in query):
        raise ValueError
    if "job" not in query:
        return None
    raw = query["job"]
    if not raw or len(raw) > 19 or not raw.isascii() or not raw.isdecimal():
        raise ValueError
    value = int(raw)
    if not 1 <= value <= MAX_ID:
        raise ValueError
    return value


def _metadata(pk, job_id, *, include_fingerprint=False):
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise DatabaseError("A standalone PostgreSQL snapshot is required")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '5s'")
        channel = (
            DataChannel.objects.filter(
                pk=pk,
                slug=profile.SLUG,
                kind="file",
                region="EU",
                url=profile.URL,
                access_approved=True,
            )
            .only("id", "name", "config", "attribution", "url", "kind", "region")
            .first()
        )
        if (
            channel is None
            or channel.config != profile.CONFIG
            or not isinstance(channel.attribution, str)
            or not 0 < len(channel.attribution) <= 2000
        ):
            return None
        jobs = DownloadJob.objects.filter(
            channel_id=pk,
            status="complete",
            expires_at__gt=timezone.now(),
            url=profile.URL,
            config_hash=configuration(channel),
        ).order_by("-created_at", "-pk")
        if job_id is not None:
            fields = COPY_FIELDS
            if include_fingerprint:
                fields += ("channel_id", "url", "config_hash", "status", "offset", "etag")
            selected = jobs.filter(pk=job_id).values(*fields).first()
            if selected is None:
                return None
            copies = []
        else:
            selected = None
            copies = list(jobs.values(*COPY_FIELDS)[:10])
        # Materialize everything inside the snapshot; templates never trigger ORM reads.
        metadata = {
            "channel": {"id": channel.pk, "name": channel.name, "attribution": channel.attribution},
            "copies": copies,
            "selected": selected,
        }
        if include_fingerprint:
            if (
                selected["total_bytes"] != profile.SIZE
                or selected["offset"] != profile.SIZE
                or selected["sha256"] != SHA256
            ):
                raise ReportRefused("invalid")
            expected = (profile.SIZE + CHUNK_BYTES - 1) // CHUNK_BYTES
            chunks = list(
                DataSnapshot.objects.filter(download_job_id=job_id)
                .order_by("pk")
                .values_list(
                    "pk",
                    "channel_id",
                    "fetched_at",
                    "expires_at",
                    "sha256",
                    "size_bytes",
                    "media_type",
                    "summary",
                )[: expected + 1]
            )
            if len(chunks) != expected or any(
                chunk[1] != pk or chunk[3] <= timezone.now() for chunk in chunks
            ):
                raise ReportRefused("invalid")
            metadata["fingerprint"] = chunks
        return metadata


def _require(condition):
    if not condition:
        raise ValueError("Invalid Natura report schema or provenance")


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _counts(value, keys, maximum):
    _require(type(value) is dict)
    _require(
        all(
            type(key) is str
            and len(key) <= MAX_FIELD_CHARS
            and "\x00" not in key
            and (keys is None or key in keys)
            and _integer(count, 1, maximum)
            for key, count in value.items()
        )
    )


def _validate_report(summary, metadata):
    job, channel = metadata["selected"], metadata["channel"]
    constants = {
        "job_id": job["id"],
        "channel_id": channel["id"],
        "source_sha256": job["sha256"],
        "source_bytes": job["total_bytes"],
        "source_url": profile.URL,
        "attribution": channel["attribution"],
        "copied_at": job["created_at"].isoformat(),
        "expires_at": job["expires_at"].isoformat(),
        "license": "CC BY 4.0 (DG ENV)",
        "dataset_uuid": profile.CONFIG["dataset_uuid"],
        "release": profile.CONFIG["release"],
        "retention_basis": "local_copy_age",
        "structure_verified": True,
        "fields": FIELDS,
        "current_registry_confirmed": False,
        "national_completeness_confirmed": False,
        "geometry_included": False,
        "history_30_days_applicable": False,
        "coordinate_reference": "ETRS89 site centres; provider feature catalogue",
        "date_precision": "source year-month semantics; raw CSV dates retained",
    }
    dynamic = {
        "records",
        "unique_sitecodes",
        "poland_records",
        "max_field_chars",
        "country_counts",
        "country_counts_raw",
        "type_counts_raw",
        "blank_counts",
        "date_ranges",
        "quality_counts",
        "quality_examples",
        "quality_status",
    }
    _require(type(summary) is dict and set(summary) == set(constants) | dynamic)
    _require(
        all(
            type(summary[key]) is type(value) and summary[key] == value
            for key, value in constants.items()
        )
    )
    records = summary["records"]
    _require(_integer(records, 1, MAX_ROWS))
    _require(type(summary["unique_sitecodes"]) is int and summary["unique_sitecodes"] == records)
    _require(_integer(summary["poland_records"], 0, records))
    _require(_integer(summary["max_field_chars"], 1, MAX_FIELD_CHARS))
    for key, allowed in (
        ("country_counts", COUNTRIES),
        ("country_counts_raw", None),
        ("type_counts_raw", None),
        ("blank_counts", FIELDS),
    ):
        _counts(summary[key], allowed, records)
    canonical = Counter()
    for country, count in summary["country_counts_raw"].items():
        _require(country.upper() in COUNTRIES)
        canonical[country.upper()] += count
    _require(dict(canonical) == summary["country_counts"])
    _require(
        sum(canonical.values()) == records and sum(summary["type_counts_raw"].values()) == records
    )
    _require(summary["poland_records"] == canonical["PL"])
    numeric = {"LATITUDE", "LONGITUDE", "AREAHA", "LENGTHKM", "MARINE_AREA_PERCENTAGE"}
    quality_keys = {"COUNTRY_CODE_noncanonical_case"}
    quality_keys.update(
        field + "_" + reason
        for field in FIELDS
        if field.startswith("DATE_")
        for reason in ("invalid_date", "after_release", "early_date_unconfirmed")
    )
    quality_keys.update(
        field + "_" + reason for field in numeric for reason in ("invalid_number", "out_of_range")
    )
    quality_keys.update({"LATITUDE_zero", "LONGITUDE_zero", "AREAHA_unknown_minus99"})
    _counts(summary["quality_counts"], quality_keys, records)
    _require(
        summary["quality_status"]
        == ("warn" if summary["quality_counts"] or summary["blank_counts"] else "pass")
    )
    noncanonical = sum(
        count
        for country, count in summary["country_counts_raw"].items()
        if country != country.upper()
    )
    _require(summary["quality_counts"].get("COUNTRY_CODE_noncanonical_case", 0) == noncanonical)
    _validate_dates(summary, records)
    examples = summary["quality_examples"]
    example_count = sum(
        count
        for key, count in summary["quality_counts"].items()
        if key != "COUNTRY_CODE_noncanonical_case"
    )
    _require(type(examples) is list and len(examples) == min(10, example_count))
    for example in examples:
        _require(
            type(example) is dict
            and set(example)
            in (
                {"sitecode", "field", "raw"},
                {"sitecode", "field", "raw", "raw_truncated"},
            )
        )
        _require(
            type(example["sitecode"]) is str
            and re.fullmatch(r"[A-Z]{2}[A-Za-z0-9]{1,62}", example["sitecode"]) is not None
        )
        _require(example["sitecode"][:2] in COUNTRIES)
        _require(type(example["field"]) is str and example["field"] in FIELDS)
        _require(any(key.startswith(example["field"] + "_") for key in summary["quality_counts"]))
        _require(
            type(example["raw"]) is str
            and len(example["raw"]) <= 200
            and "\x00" not in example["raw"]
        )
        if "raw_truncated" in example:
            _require(example["raw_truncated"] is True and len(example["raw"]) == 200)


def _raw_date(raw):
    _require(type(raw) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw) is not None)
    return date.fromisoformat(raw)


def _validate_dates(summary, records):
    fields = {field for field in FIELDS if field.startswith("DATE_")}
    spans, quality = summary["date_ranges"], summary["quality_counts"]
    _require(type(spans) is dict and set(spans) <= fields)
    for field in fields:
        count = 0
        if field in spans:
            span = spans[field]
            _require(
                type(span) is dict and set(span) == {"min_raw", "max_raw", "count", "after_release"}
            )
            low, high = _raw_date(span["min_raw"]), _raw_date(span["max_raw"])
            count = span["count"]
            _require(low <= high and _integer(count, 1, records))
            _require(_integer(span["after_release"], 0, count))
            _require(span["after_release"] == quality.get(field + "_after_release", 0))
            if high <= date(2021, 12, 31):
                _require(span["after_release"] == 0)
            elif low > date(2021, 12, 31):
                _require(span["after_release"] == count)
            else:
                _require(0 < span["after_release"] < count)
        else:
            _require(quality.get(field + "_after_release", 0) == 0)
        _require(
            count + summary["blank_counts"].get(field, 0) + quality.get(field + "_invalid_date", 0)
            == records
        )
        _require(quality.get(field + "_early_date_unconfirmed", 0) <= count)


def verified_report(pk, job_id):
    """N3 boundary; the HTTP caller must authorize staff before invoking it.

    Raises ValueError for invalid IDs and ReportRefused(missing/invalid or runner
    codes). No payload reads in the parent; the runner uses the canonical reader.
    Each metadata read is a separate standalone snapshot, not a long transaction.
    The HTTP view computes a report only for an explicit, validated job ID.
    """
    if any(type(value) is not int or not 1 <= value <= MAX_ID for value in (pk, job_id)):
        raise ValueError("Positive int64 identifiers required")
    try:
        before = _metadata(pk, job_id, include_fingerprint=True)
        if before is None:
            raise ReportRefused("missing")
        summary = natura_runner.run_report(job_id)
        try:
            _validate_report(summary, before)
        except (ValueError, TypeError, KeyError) as error:
            raise ReportRefused("invalid") from error
        after = _metadata(pk, job_id, include_fingerprint=True)
        if after is None or after != before:
            raise ReportRefused("invalid")
    except DatabaseError as error:
        raise ReportRefused("unavailable") from error
    after.pop("fingerprint")
    return {**after, "summary": summary}


@never_cache
@_nosniff
@staff_member_required(login_url="login")
@require_GET
def report(request, pk):
    context = {"source_url": SOURCE_URL, "file_url": profile.URL, "license_url": LICENSE_URL}
    status = 200
    try:
        if type(pk) is not int or not 1 <= pk <= MAX_ID:
            raise ValueError
        job_id = _job_id(request.GET)
    except ValueError:
        status = 400
        context["error"] = (
            "Nieprawidłowy numer lub parametr. Dozwolony jest tylko pojedynczy dodatni job."
        )
    else:
        try:
            metadata = _metadata(pk, None) if job_id is None else verified_report(pk, job_id)
        except (DatabaseError, ReportRefused) as error:
            code = error.code if isinstance(error, ReportRefused) else "unavailable"
            status, context["error"] = {
                "missing": (
                    404,
                    "Brak zatwierdzonego kanału lub wskazanej ważnej kopii. Nie wybieramy innej.",
                ),
                "invalid": (
                    409,
                    "Raport odrzucony: integralność, wynik lub metadane kopii "
                    "nie zostały potwierdzone. Nie pokazujemy częściowego wyniku.",
                ),
            }.get(
                code,
                (
                    503,
                    "Raport jest chwilowo niedostępny lub zajęty. "
                    "Spróbuj ponownie ręcznie za kilka sekund.",
                ),
            )
        else:
            if metadata is None:
                status = 404
                context["error"] = (
                    "Brak zatwierdzonego kanału lub wskazanej ważnej kopii. Nie wybieramy innej."
                )
            else:
                context.update(metadata)
    response = render(request, "natura_report.html", context, status=status)
    if status == 503:
        response["Retry-After"] = "5"
    return response
