"""Bounded, unsigned historical collection receipt; B1 is stdout preview only."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from ingestion.collection_continuity import PILOT_END, PILOT_START, pilot_window_policy
from ingestion.collection_evidence import IMGW_URLS, METADATA_RETENTION_DAYS, read_attempts
from ingestion.downloads import configuration
from ingestion.models import DataChannel

MAX_RECEIPT_BYTES = 65536
FAILURE = "Collection receipt unavailable: scope, evidence, time, format or budget."
ASSESSMENT_FIELDS = (
    *pilot_window_policy(),
    "status",
    "continuity_confirmed",
    "window_complete",
    "observed_until",
    "worker_attempts",
    "boundary_carry_in_attempts",
    "late_attempts",
    "max_lateness_seconds",
    "max_gap_over_interval_seconds",
    "leading_gap_seconds",
    "trailing_gap_seconds",
    "reasons",
)
STATUS_ORDER = ("unconfirmed", "failed", "pending", "pass")


class ReceiptError(ValueError):
    """Safe refusal without exposing configuration, provider errors or SQL."""


def _utc_text(stamp):
    # DjangoJSONEncoder truncates microseconds: preserve the actual assessment boundary.
    if not isinstance(stamp, datetime) or timezone.is_naive(stamp):
        raise ValueError("Aware timestamp required")
    return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def encode_receipt(receipt):
    """Canonical UTF-8 receipt hash; bounded envelope with one final LF, not a signature."""
    try:
        options = {
            "cls": DjangoJSONEncoder,
            "sort_keys": True,
            "ensure_ascii": False,
            "separators": (",", ":"),
            "allow_nan": False,
        }
        canonical = json.dumps(receipt, **options).encode("utf-8")
        envelope = {
            "receipt": receipt,
            "receipt_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        output = json.dumps(envelope, **options) + "\n"
        if len(output.encode("utf-8")) > MAX_RECEIPT_BYTES:
            raise ValueError("Receipt budget exceeded")
        return output
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise ReceiptError(FAILURE) from None


def preview(channel_ids):
    """One local as_of and one read-only scope/evidence snapshot; never writes files."""
    if settings.COLLECTION_EVIDENCE_ENABLED is not True:
        raise ReceiptError(FAILURE)
    if (
        type(channel_ids) not in (list, tuple)
        or len(channel_ids) != 3
        or any(type(pk) is not int or not 1 <= pk < 2**63 for pk in channel_ids)
        or len(set(channel_ids)) != 3
    ):
        raise ReceiptError(FAILURE)
    try:
        # SET TRANSACTION must establish our own snapshot, not alter a caller's transaction.
        if connection.in_atomic_block or not connection.get_autocommit():
            raise ValueError("Existing transaction")
        now = timezone.now()
        if not isinstance(now, datetime) or timezone.is_naive(now):
            raise ValueError("Aware clock required")
        now = now.astimezone(UTC)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '15s'")
            channels = list(
                DataChannel.objects.filter(pk__in=channel_ids)
                .order_by("pk")
                .values(
                    "id",
                    "slug",
                    "kind",
                    "region",
                    "url",
                    "config",
                    "interval_minutes",
                    "enabled",
                    "access_approved",
                )[:4]
            )
            if len(channels) != 3 or {channel["url"] for channel in channels} != IMGW_URLS:
                raise ValueError("Complete reviewed scope required")
            if any(
                channel["kind"] != "imgw"
                or channel["region"] != "PL"
                or channel["enabled"] is not True
                or channel["access_approved"] is not True
                or not isinstance(channel["config"], dict)
                or channel["config"].get("mode")
                or channel["config"].get("pagination")
                for channel in channels
            ):
                raise ValueError("Approved scope required")
            evidence = read_attempts(channels, now=now, imgw_pilot_window=True)
            if evidence["status"] not in ("available", "capped", "unavailable"):
                raise ValueError("Unexpected evidence state")
            rows = []
            for channel in channels:
                assessment = None
                if evidence["status"] == "available":
                    raw = evidence["acceptance_by_channel"][channel["id"]]
                    assessment = {key: raw[key] for key in ASSESSMENT_FIELDS}
                    status = assessment["status"]
                    if (
                        status not in STATUS_ORDER
                        or type(assessment["continuity_confirmed"]) is not bool
                    ):
                        raise ValueError("Invalid assessment")
                    if assessment["continuity_confirmed"] != (status == "pass"):
                        raise ValueError("Inconsistent confirmation")
                    if status == "pass" and (
                        now < PILOT_END or assessment["window_complete"] is not True
                    ):
                        raise ValueError("Premature confirmation")
                    for field in ("window_start", "window_end", "observed_until"):
                        assessment[field] = _utc_text(assessment[field])
                rows.append(
                    {
                        "channel_id": channel["id"],
                        "slug": channel["slug"],
                        "profile_sha256": configuration(SimpleNamespace(**channel)),
                        "interval_minutes": channel["interval_minutes"],
                        "assessment": assessment,
                    }
                )
        statuses = {
            row["assessment"]["status"] if row["assessment"] is not None else "unconfirmed"
            for row in rows
        }
        receipt = {
            "schema_version": 1,
            "format": "geoklimat.imgw-collection-receipt",
            "evaluator_version": "imgw-pilot-window-v1",
            "as_of": _utc_text(now),
            "policy": pilot_window_policy(),
            "metadata_retention_days": METADATA_RETENTION_DAYS,
            "live_reassessment_deadline": PILOT_START + timedelta(days=METADATA_RETENTION_DAYS),
            "evidence_status": evidence["status"],
            "assessment_status": next(status for status in STATUS_ORDER if status in statuses),
            "channels": rows,
            "basis": "terminal_worker_collection_metadata",
            "scope": "collection_only_three_imgw_pl_api_profiles",
            "limitations": [
                "historical_as_of_only",
                "not_observation_coverage",
                "not_cache_integrity",
                "not_fleet_acceptance",
                "unsigned_local_record",
            ],
        }
        return encode_receipt(receipt)
    except (DatabaseError, ValueError, TypeError, KeyError, UnicodeError, OverflowError):
        raise ReceiptError(FAILURE) from None
