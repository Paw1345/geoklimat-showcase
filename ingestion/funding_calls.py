"""Daily EU funding calls (registry 189): LIFE, Horizon Europe clusters 5-6 and other
EU programmes kept whole (profile "programmes", P4 2026-10-15).

Each run reads the whole fixed public search (funding_search.discover) and stores one
copy per profile. The call state is derived only from the source start and deadline
dates at collection time; the index status codes are kept raw because the probe of
2026-10-01 found "open/forthcoming" records whose deadlines had all passed. A future
later deadline after an elapsed one (two-stage or cut-off calls) is not reported as
open: eligibility of new applicants is never inferred.
"""

import json
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from ingestion.funding_search import SCOPE, discover
from ingestion.funding_search_transport import BASE, PROFILES
from ingestion.transport import FetchError

REGISTRY_ID = 189
PORTAL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/calls-for-proposals"
TITLES = {
    "life": "Nabory LIFE (otwarte i zapowiedziane)",
    "horizon": "Nabory Horyzont Europa — klastry 5 i 6",
    "programmes": "Nabory innych programów UE (CEF, Cyfrowa Europa, EFMRA, I3, JTM, RENEWFM)",
}
STATES = ("open", "forthcoming", "later_deadline_only", "deadline_passed")
STATE_RULE = (
    "open: start passed and earliest deadline in the future; forthcoming: start "
    "in the future; later_deadline_only: an earlier deadline passed, a later one "
    "remains (new applicants unverified); deadline_passed: all deadlines passed."
)
ATTRIBUTION = (
    "Komisja Europejska — Funding & Tenders Portal, publiczne wyszukiwanie SEDIA "
    "(tylko odczyt, bez logowania). Stan naboru wyliczony z dat źródła w chwili pobrania; "
    "kody statusu indeksu zachowane bez interpretacji. Szczegóły i warunki: " + PORTAL
)


def slug(key):
    return "eu-funding-" + key


def config(key):
    return {"mode": "funding_calls", "profile": key}


def profile(channel):
    key = channel.config.get("profile")
    if (
        key not in PROFILES
        or channel.config != config(key)
        or channel.url != BASE
        or channel.kind != "table"
        or channel.region != "EU"
    ):
        raise FetchError("Funding calls channel outside reviewed profile")
    return key


def call_state(record, now):
    starts = [datetime.fromisoformat(value) for value in record["start_dates_raw"]]
    deadlines = [datetime.fromisoformat(value) for value in record["deadline_dates_raw"]]
    if all(deadline <= now for deadline in deadlines):
        return "deadline_passed"
    if min(starts) > now:
        return "forthcoming"
    if min(deadlines) > now:
        return "open"
    return "later_deadline_only"


def programme_counts(rows):
    counts = {}
    for row in rows:
        prefix = row["identifier"].split("-", 1)[0]
        counts[prefix] = counts.get(prefix, 0) + 1
    return dict(sorted(counts.items()))


def collect(channel, client, now=None):
    key = profile(channel)
    now = now or timezone.now()
    report = discover(client, now, key)
    rows = []
    for record in report["records"]:
        rows.append(
            {
                "identifier": record["identifier"],
                **(
                    {"source_reference": record["source_reference"]}
                    if "source_reference" in record
                    else {}
                ),
                "title": record["title"],
                "source_url": record["source_url"],
                "state": call_state(record, now),
                "next_deadline_raw": record["deadline_time_evidence"]["next_source_deadline_raw"],
                "deadline_dates_raw": record["deadline_dates_raw"],
                "start_dates_raw": record["start_dates_raw"],
                "source_status_raw": record["source_status_raw"],
                "source_type_raw": record["source_type_raw"],
            }
        )
    counts = {state: sum(row["state"] == state for row in rows) for state in STATES}
    # No collection time in the payload: an unchanged state set dedupes to one copy.
    payload = json.dumps(
        {
            "schema": "eu-funding-calls-v1",
            "profile": key,
            "title": TITLES[key],
            "scope": SCOPE[key],
            "source_url": BASE,
            "portal_url": PORTAL,
            "attribution": ATTRIBUTION,
            "state_rule": STATE_RULE,
            "source_total": report["total"],
            "outside_scope_count": report["outside_scope_count"],
            "state_counts": counts,
            "records": rows,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("Funding calls payload exceeds automatic budget")
    return (
        payload,
        "application/json",
        {
            "kind": "funding_calls",
            "collector": "funding_calls",
            "profile": key,
            "scope": "EU",
            "as_of": now.isoformat(),
            "records": len(rows),
            "source_count": report["total"],
            "outside_scope": report["outside_scope_count"],
            # Per-run paging evidence stays out of the payload so an unchanged set dedupes.
            "duplicate_rows_dropped": report["duplicate_rows_dropped"],
            "scan_passes": report["passes"],
            "completeness": "single_page" if report["total"] <= 50 else "converged_passes",
            **{"state_" + state: count for state, count in counts.items()},
            **({"programme_counts": programme_counts(rows)} if key == "programmes" else {}),
            "partial": False,
            "retention_basis": "local_copy_age",
        },
    )
