"""Internal terminal writer, IMGW capture and bounded metadata reader.

Caller-supplied origin is not a trust boundary: the private C4 worker scope
establishes provenance. Never pass request/CLI dictionaries to the writer.
The default OFF path must work without the ledger table, even with a broken DB.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType, SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from ingestion.models import CollectionAttempt

logger = logging.getLogger(__name__)
IMGW_URLS = {
    f"https://danepubliczne.imgw.pl/api/data/{profile}" for profile in ("synop", "meteo", "hydro")
}
_WORKER_BATCH = ContextVar("collection_evidence_worker_batch", default=None)
READ_ATTEMPT_LIMIT = 10_000
RETENTION_BATCH = 500
RETENTION_MAX_BATCH = 1000
METADATA_RETENTION_DAYS = 35


def prune_attempts(*, apply=False, now=None, limit=RETENTION_BATCH):
    """Bounded metadata-only retention; OFF before connection/SQL."""

    def refused(status):
        return {"status": status, "deleted": 0, "applied": False}

    if settings.COLLECTION_EVIDENCE_ENABLED is not True:
        return refused("disabled")
    if type(apply) is not bool or type(limit) is not int or not 1 <= limit <= RETENTION_MAX_BATCH:
        logger.warning("collection_evidence_retention_invalid")
        return refused("invalid")
    now = timezone.now() if now is None else now
    if not isinstance(now, datetime) or timezone.is_naive(now):
        logger.warning("collection_evidence_retention_invalid")
        return refused("invalid")
    cutoff = now.astimezone(UTC) - timedelta(days=METADATA_RETENTION_DAYS)
    try:
        if connection.in_atomic_block or not connection.get_autocommit():
            return refused("transaction_active")
        with transaction.atomic(durable=True):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_total_relation_size(to_regclass(%s))",
                    [CollectionAttempt._meta.db_table],
                )
                relation_bytes = cursor.fetchone()[0]
            expired = CollectionAttempt.objects.filter(finished_at__lt=cutoff)
            ids = list(
                expired.order_by("finished_at", "pk").values_list("pk", flat=True)[: limit + 1]
            )
            candidates = ids[:limit]
            deleted = 0
            if apply and candidates:
                # Recheck age at deletion. Never delete the sentinel or cascade elsewhere.
                deleted, per_model = expired.filter(pk__in=candidates).delete()
                if any(
                    model != CollectionAttempt._meta.label and count
                    for model, count in per_model.items()
                ):
                    raise DatabaseError("Unexpected retention cascade")
        return {
            "status": "applied" if apply else "dry_run",
            "applied": apply,
            "candidates": len(candidates),
            "deleted": deleted,
            "more_expired": len(ids) > limit,
            "cutoff": cutoff,
            "limit": limit,
            "relation_bytes": relation_bytes,
        }
    except DatabaseError:
        logger.warning("collection_evidence_retention_unavailable")
        return refused("unavailable")


def read_attempts(channels, *, now, imgw_pilot_window=False):
    """One bounded metadata SELECT; legacy rolling summary never asserts PASS.

    Called inside the history report's read-only repeatable-read transaction.
    A savepoint isolates missing-table/read failures from observation reporting.
    A cap refuses *all* aggregates rather than exporting incomplete zero counts.
    Explicit pilot assessment includes its left boundary in the same SELECT,
    without changing the legacy (rolling_start, as_of] aggregation contract.
    """
    if settings.COLLECTION_EVIDENCE_ENABLED is not True:
        return {"status": "disabled", "by_channel": {}}
    if timezone.is_naive(now):
        raise ValueError("Aware evidence report time required")
    now = now.astimezone(UTC)
    eligible = {
        channel["id"]: channel
        for channel in channels
        if channel["kind"] == "imgw" and channel["region"] == "PL" and channel["url"] in IMGW_URLS
    }
    if not eligible:
        return {"status": "not_applicable", "by_channel": {}}

    from ingestion.downloads import configuration
    from ingestion.observations import retention_window

    start = now - retention_window()
    metadata_start = now - timedelta(days=METADATA_RETENTION_DAYS)
    query_start = start
    if imgw_pilot_window:
        from ingestion.collection_continuity import PILOT_START

        query_start = max(min(start, PILOT_START), metadata_start)

    def refused(status):
        return {
            "status": status,
            "by_channel": {
                pk: {"status": status, "continuity_confirmed": False} for pk in eligible
            },
        }

    try:
        if any(not isinstance(channel["config"], dict) for channel in eligible.values()):
            raise ValueError("Profile mapping required")
        eligible = {
            pk: channel
            for pk, channel in eligible.items()
            if not channel["config"].get("mode") and not channel["config"].get("pagination")
        }
        if not eligible:
            return {"status": "not_applicable", "by_channel": {}}
        profiles = {
            pk: configuration(SimpleNamespace(**channel)) for pk, channel in eligible.items()
        }
        with transaction.atomic():
            attempts = CollectionAttempt.objects.filter(
                channel_id__in=eligible, started_at__lte=now
            )
            attempts = (
                attempts.filter(started_at__gte=query_start)
                if imgw_pilot_window
                else attempts.filter(started_at__gt=start)
            )
            rows = list(
                attempts.order_by("channel_id", "started_at", "pk").values(
                    "channel_id",
                    "origin",
                    "outcome",
                    "started_at",
                    "finished_at",
                    "due_at",
                    "interval_minutes",
                    "profile_sha256",
                )[: READ_ATTEMPT_LIMIT + 1]
            )
    except (DatabaseError, ValueError, TypeError):
        logger.warning("collection_evidence_read_unavailable")
        return refused("unavailable")
    if len(rows) > READ_ATTEMPT_LIMIT:
        return refused("capped")
    grouped = {pk: [] for pk in eligible}
    for row in rows:
        grouped[row["channel_id"]].append(row)
    result = {
        "status": "available",
        "by_channel": {
            pk: _attempt_summary(
                [row for row in grouped[pk] if row["started_at"] > start],
                profiles[pk],
                channel["interval_minutes"],
                start,
                now,
            )
            for pk, channel in eligible.items()
        },
    }
    if imgw_pilot_window:
        from ingestion.collection_continuity import assess_pilot_window

        result["acceptance_by_channel"] = {
            pk: assess_pilot_window(
                grouped[pk],
                now=now,
                current_profile=profiles[pk],
                current_interval=channel["interval_minutes"],
                retained_start=metadata_start,
            )
            for pk, channel in eligible.items()
        }
    return result


def _attempt_summary(rows, current_profile, current_interval, start, now):
    origins = {
        origin: {
            "attempts": 0,
            "outcomes": dict.fromkeys(("stored", "unchanged", "failed", "busy"), 0),
            "attempts_by_day": {},
            "first_started_at": None,
            "last_finished_at": None,
            "max_lateness_seconds": None,
            "max_start_gap_seconds": None,
            "max_gap_over_interval_seconds": None,
            "early_start_count": 0,
            "missing_due_count": 0,
        }
        for origin in ("worker", "manual", "unknown")
    }
    profiles, intervals, previous = set(), set(), {}
    mismatched_profile, mismatched_interval, unfinished = 0, 0, 0
    for row in rows:
        if row["finished_at"] > now:
            unfinished += 1
            continue
        origin = origins[row["origin"]]
        origin["attempts"] += 1
        origin["outcomes"][row["outcome"]] += 1
        stamp = row["started_at"]
        day = stamp.astimezone(ZoneInfo("Europe/Warsaw")).date().isoformat()
        origin["attempts_by_day"][day] = origin["attempts_by_day"].get(day, 0) + 1
        origin["first_started_at"] = origin["first_started_at"] or stamp
        origin["last_finished_at"] = max(
            origin["last_finished_at"] or row["finished_at"], row["finished_at"]
        )
        if row["due_at"] is None:
            origin["missing_due_count"] += 1
        else:
            lag = (stamp - row["due_at"]).total_seconds()
            origin["early_start_count"] += int(lag < 0)
            origin["max_lateness_seconds"] = max(origin["max_lateness_seconds"] or 0, lag, 0)
        prior = previous.get(row["origin"])
        if prior is not None:
            gap = (stamp - prior["started_at"]).total_seconds()
            origin["max_start_gap_seconds"] = max(origin["max_start_gap_seconds"] or 0, gap)
            if (prior["profile_sha256"], prior["interval_minutes"]) == (
                row["profile_sha256"],
                row["interval_minutes"],
            ):
                origin["max_gap_over_interval_seconds"] = max(
                    origin["max_gap_over_interval_seconds"] or 0,
                    gap - prior["interval_minutes"] * 60,
                    0,
                )
        previous[row["origin"]] = row
        profiles.add(row["profile_sha256"])
        intervals.add(row["interval_minutes"])
        mismatched_profile += int(row["profile_sha256"] != current_profile)
        mismatched_interval += int(row["interval_minutes"] != current_interval)
    reasons = ["tolerance_not_agreed"]
    if not origins["worker"]["attempts"]:
        reasons.append("no_worker_attempts")
    if mismatched_profile or len(profiles) > 1:
        reasons.append("profile_changed")
    if mismatched_interval or len(intervals) > 1:
        reasons.append("interval_changed")
    if unfinished:
        reasons.append("not_finished_as_of")
    return {
        "status": "available"
        if any(origin["attempts"] for origin in origins.values())
        else "no_evidence",
        "window_start": start,
        "window_end": now,
        "window_basis": "started_at_gt_start_lte_as_of",
        "day_zone": "Europe/Warsaw",
        "continuity_confirmed": False,
        "tolerance_seconds": None,
        "reasons": reasons,
        "by_origin": origins,
        "current_interval_minutes": current_interval,
        "distinct_profile_count": len(profiles),
        "profile_mismatch_count": mismatched_profile,
        "distinct_interval_count": len(intervals),
        "interval_mismatch_count": mismatched_interval,
        "not_finished_as_of_count": unfinished,
    }


@contextmanager
def _worker_attempt_scope():
    """Internal worker data-step provenance, not a public origin/UUID argument.

    No batch completion is recorded. This is an application call-path boundary,
    not protection against code execution or direct database administration.
    """
    from ingestion.worker_stop import STOP

    if settings.COLLECTION_EVIDENCE_ENABLED is not True or STOP.is_set():
        yield
        return
    try:
        batch_id = uuid4()
    except Exception:
        logger.warning("collection_evidence_worker_context_unavailable")
        yield
        return
    token = _WORKER_BATCH.set(batch_id)
    try:
        yield
    finally:
        _WORKER_BATCH.reset(token)


@dataclass(frozen=True)
class AttemptCapture:
    # None values mean capture failed; a None capture means bypass/OFF/dry-run.
    values: MappingProxyType | None


def begin_attempt(channel, *, dry_run, manual=False):
    if settings.COLLECTION_EVIDENCE_ENABLED is not True or dry_run:
        return None
    try:
        if channel.kind != "imgw" or channel.region != "PL" or channel.url not in IMGW_URLS:
            return None
        if channel.config.get("mode") or channel.config.get("pagination"):
            return None
        if type(manual) is not bool:
            raise ValidationError("Internal boolean provenance required")
        # Canonical hash, imported lazily to avoid downloads/data_service cycles.
        from ingestion.downloads import configuration

        batch_id = None if manual else _WORKER_BATCH.get()
        return AttemptCapture(
            MappingProxyType(
                {
                    "channel_id": channel.pk,
                    "attempt_id": uuid4(),
                    "origin": "manual"
                    if manual
                    else "worker"
                    if batch_id is not None
                    else "unknown",
                    "batch_id": batch_id,
                    "started_at": timezone.now(),
                    "due_at": channel.next_fetch_at,
                    "interval_minutes": channel.interval_minutes,
                    "profile_sha256": configuration(channel),
                }
            )
        )
    except Exception:
        logger.warning("collection_evidence_capture_unavailable")
        return AttemptCapture(None)


def finish_attempt(capture, outcome):
    if settings.COLLECTION_EVIDENCE_ENABLED is not True or capture is None:
        return "disabled"
    if capture.values is None:
        return "unavailable"
    if outcome not in {"stored", "unchanged", "failed", "busy"}:
        logger.warning("collection_evidence_not_terminal")
        return "not_terminal"
    try:
        return record_attempt(
            **capture.values,
            finished_at=timezone.now(),
            outcome=outcome,
            reason_code={"failed": "collection_failed", "busy": "lock_busy"}.get(outcome, ""),
        )
    except Exception:
        # An unexpected ledger error must not convert successful data into failed data.
        logger.warning("collection_evidence_unavailable")
        return "unavailable"


def record_attempt(
    *,
    channel_id,
    attempt_id,
    batch_id,
    origin,
    started_at,
    finished_at,
    due_at,
    interval_minutes,
    profile_sha256,
    outcome,
    reason_code,
):
    """Return a safe storage status, never a collector result or continuity PASS."""
    if settings.COLLECTION_EVIDENCE_ENABLED is not True:
        return "disabled"
    try:
        # Do not couple metadata failure/commit to the collector's transaction.
        if connection.in_atomic_block or not connection.get_autocommit():
            logger.warning("collection_evidence_transaction_active")
            return "transaction_active"
        for stamp in (started_at, finished_at, due_at):
            if stamp is not None and (not isinstance(stamp, datetime) or timezone.is_naive(stamp)):
                raise ValidationError("Aware timestamp required")
        entry = CollectionAttempt(
            channel_id=channel_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
            origin=origin,
            started_at=started_at,
            finished_at=finished_at,
            due_at=due_at,
            interval_minutes=interval_minutes,
            profile_sha256=profile_sha256,
            outcome=outcome,
            reason_code=reason_code,
        )
        # Field/UTC validation without ledger reads. PG owns FK/uniqueness/checks.
        entry.full_clean(exclude=["channel"], validate_unique=False, validate_constraints=False)
        with transaction.atomic(durable=True):
            entry.save(force_insert=True)
    except ValidationError:
        logger.warning("collection_evidence_invalid_metadata")
        return "invalid"
    except DatabaseError:
        # No exception text, traceback, URLs or credentials in the audit/log.
        logger.warning("collection_evidence_unavailable")
        return "unavailable"
    return "recorded"
