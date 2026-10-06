"""UTC calendar bins describe local evidence, not supplier cadence or outages."""

from datetime import UTC, datetime, time, timedelta

from ingestion.observations import retention_window


def timeline(scope, now, days, oldest=None, newest=None):
    now = now.astimezone(UTC)
    active_start = now - retention_window()
    day = active_start.date()
    rows = []
    counts_available = days is not None
    while day <= now.date():
        midnight = datetime.combine(day, time(), tzinfo=UTC)
        tomorrow = midnight + timedelta(days=1)
        begin, end = max(midnight, active_start), min(tomorrow, now)
        if begin < end:
            overlap = scope["search_start"] < end and scope["search_end"] >= begin
            full = scope["search_start"] <= begin and scope["search_end"] >= end
            search_state = (
                "within_frozen_search"
                if full
                else "partly_searched"
                if overlap
                else "outside_frozen_search"
            )
            observed = (
                days.get(day.isoformat(), {"occurrences": 0, "ids": set()})
                if counts_available
                else None
            )
            rows.append(
                {
                    "utc_day": day.isoformat(),
                    "active_interval_start": begin,
                    "active_interval_end": end,
                    "partial_calendar_day": begin != midnight or end != tomorrow,
                    "search_state": search_state,
                    "record_occurrences_in_manifest": observed["occurrences"] if observed else None,
                    "distinct_scene_ids_in_manifest_day": len(observed["ids"])
                    if observed
                    else None,
                    "no_observations_in_manifest": observed["occurrences"] == 0
                    if observed
                    else None,
                    "source_missing_data_confirmed": False,
                }
            )
        day += timedelta(days=1)
    return {
        "zone": "UTC",
        "active_start": active_start,
        "active_end": now,
        "observations_available_for_entire_manifest": counts_available,
        "oldest_verified_observation": oldest if counts_available else None,
        "newest_verified_observation": newest if counts_available else None,
        "daily_cadence_verified": False,
        "days": rows,
        "days_outside_frozen_search": sum(
            r["search_state"] == "outside_frozen_search" for r in rows
        ),
        "days_partly_searched": sum(r["search_state"] == "partly_searched" for r in rows),
        "days_without_observations_in_manifest": sum(
            r["no_observations_in_manifest"] is True for r in rows
        )
        if counts_available
        else None,
    }
