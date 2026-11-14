"""Pure assessment of the explicitly approved IMGW pilot window, not observations."""

from datetime import UTC, datetime, timedelta

PILOT_START = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
PILOT_END = PILOT_START + timedelta(days=30)
PILOT_INTERVAL_SECONDS = 3600
PILOT_TOLERANCE_SECONDS = 300


def pilot_window_policy():
    return {
        "name": "imgw-pilot-2026-10-15",
        "window_start": PILOT_START,
        "window_end": PILOT_END,
        "required_days": 30,
        "tolerance_seconds": PILOT_TOLERANCE_SECONDS,
        "interval_minutes": 60,
    }


def assess_pilot_window(rows, *, now, current_profile, current_interval, retained_start):
    """Only successful terminal worker evidence can prove the whole named interval.

    The first overdue carry-in belongs to the historical window, not to the
    accepted post-resume segment. Retention loss refuses a historical PASS.
    This function has no SQL, fetching, clock reads, or state mutation.
    """
    if now.utcoffset() is None or retained_start.utcoffset() is None:
        raise ValueError("Aware assessment timestamps required")
    now = now.astimezone(UTC)
    until = min(now, PILOT_END)
    result = pilot_window_policy() | {
        "status": "unconfirmed",
        "continuity_confirmed": False,
        "window_complete": now >= PILOT_END,
        "observed_until": until,
        "worker_attempts": 0,
        "boundary_carry_in_attempts": 0,
        "late_attempts": 0,
        "max_lateness_seconds": None,
        "max_gap_over_interval_seconds": None,
        "leading_gap_seconds": None,
        "trailing_gap_seconds": None,
        "reasons": [],
    }
    if now < PILOT_START:
        return result | {"status": "pending", "reasons": ["window_not_started"]}
    if retained_start > PILOT_START:
        return result | {"reasons": ["window_start_not_retained"]}
    workers = sorted(
        (
            row
            for row in rows
            if row["origin"] == "worker" and PILOT_START <= row["started_at"] <= until
        ),
        key=lambda row: row["started_at"],
    )
    finished = [row for row in workers if row["finished_at"] <= now]
    unknown = set()
    violations = set()
    if len(finished) != len(workers):
        unknown.add("unfinished_worker_attempt")
    if not finished:
        unknown.add("no_worker_attempts")
    result["worker_attempts"] = len(finished)
    if current_interval != 60:
        violations.add("interval_changed")
    for index, row in enumerate(finished):
        if row["outcome"] not in {"stored", "unchanged"}:
            violations.add("worker_outcome_not_successful")
        if row["profile_sha256"] != current_profile:
            violations.add("profile_changed")
        if row["interval_minutes"] != 60:
            violations.add("interval_changed")
        due = row["due_at"]
        if due is None:
            violations.add("missing_due")
        elif index == 0 and due < PILOT_START:
            result["boundary_carry_in_attempts"] += 1
        else:
            lag = (row["started_at"] - due).total_seconds()
            if lag < 0:
                violations.add("early_start")
            if lag > PILOT_TOLERANCE_SECONDS:
                result["late_attempts"] += 1
                violations.add("late_worker_attempt")
            result["max_lateness_seconds"] = max(result["max_lateness_seconds"] or 0, lag, 0)
    if finished:
        leading = (finished[0]["started_at"] - PILOT_START).total_seconds()
        trailing = (until - finished[-1]["started_at"]).total_seconds()
        result["leading_gap_seconds"] = leading
        result["trailing_gap_seconds"] = trailing
        if max(leading, trailing) > PILOT_INTERVAL_SECONDS + PILOT_TOLERANCE_SECONDS:
            violations.add("window_edge_gap")
        excess = max(
            (
                (right["started_at"] - left["started_at"]).total_seconds() - PILOT_INTERVAL_SECONDS
                for left, right in zip(finished, finished[1:], strict=False)
            ),
            default=0,
        )
        result["max_gap_over_interval_seconds"] = max(0, excess)
        if excess > PILOT_TOLERANCE_SECONDS:
            violations.add("worker_gap_over_tolerance")
    result["reasons"] = sorted(unknown | violations)
    if unknown:
        result["status"] = "unconfirmed"
    elif violations:
        result["status"] = "failed"
    elif not result["window_complete"]:
        result["status"] = "pending"
        result["reasons"] = ["window_not_complete"]
    else:
        result["status"] = "pass"
        result["continuity_confirmed"] = True
    return result
