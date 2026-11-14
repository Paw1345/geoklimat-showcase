"""Read-only bounded LIFE/Horizon search quality report; no automatic funding/deadline facts."""

import json
import re
from datetime import datetime

from django.utils import timezone

from ingestion.funding_search_transport import PROFILES, PROGRAMMES, request_spec
from ingestion.transport import FetchError

IDENTIFIER = re.compile(r"LIFE-[A-Za-z0-9-]{1,180}\Z")
# Other programmes: the identifier prefix names the programme and must match the row's
# programme id, so a topic cannot drift between programmes unnoticed.
PROGRAMME_ID = re.compile(r"([A-Z0-9]{2,12})-[A-Za-z0-9-]{1,180}\Z")
IDENTIFIERS = {
    "life": IDENTIFIER,
    "horizon": re.compile(r"HORIZON-[A-Za-z0-9-]{1,180}\Z"),
    "programmes": PROGRAMME_ID,
}
# Only Horizon clusters 5 (climate, energy, mobility) and 6 (food, bioeconomy,
# natural resources, agriculture, environment) are kept; user card 2026-10-08T08:12Z.
IN_SCOPE = {
    "life": re.compile(r"LIFE-"),
    "horizon": re.compile(r"HORIZON-CL[56]-"),
    "programmes": re.compile("(?:" + "|".join(sorted(PROGRAMMES)) + ")-"),
}
SCOPE = {
    "life": "LIFE2021-2027/en/status_codes31094501,31094502",
    "horizon": "HorizonEurope/clusters5-6/en/status_codes31094501,31094502",
    "programmes": "/".join(sorted(PROGRAMMES)) + "/whole/en/status_codes31094501,31094502",
}
DETAIL = "https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/"
COMPETITIVE = re.compile(re.escape(DETAIL) + r"[0-9]{1,12}COMPETITIVE_CALLen\Z")
# Read through Client 2026-10-08: the Horizon index also uses the portal's newer
# topic and competitive-call addresses, and holds ERC topics under the programme.
PORTAL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/"
PORTAL_COMPETITIVE = re.compile(re.escape(PORTAL) + r"competitive-calls-cs/[0-9]{1,12}\Z")
HORIZON_PROGRAMME_ID = re.compile(r"(?:HORIZON|ERC)-[A-Za-z0-9-]{1,180}\Z")
REFERENCE = re.compile(r"[0-9]{1,12}COMPETITIVE_CALLen\Z")
PAGE_SIZE = 50
MAX_PASSES = 5


def single(meta, key):
    values = meta.get(key)
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str):
        raise FetchError("Funding search scalar metadata changed")
    if not values[0].strip() or len(values[0]) > 1500:
        raise FetchError("Funding search metadata value outside budget")
    return values[0]


def source_dates(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise FetchError("Funding search source date list changed")
    parsed = []
    for value in values:
        if not isinstance(value, str) or len(value) > 64:
            raise FetchError("Funding search invalid source date")
        try:
            date = datetime.fromisoformat(value)
        except (ValueError, TypeError) as exc:
            raise FetchError("Funding search invalid source date") from exc
        if date.tzinfo is None or date.utcoffset() is None:
            raise FetchError("Funding search source date lacks timezone")
        parsed.append(date)
    if len(set(values)) != len(values):
        raise FetchError("Funding search duplicate source dates")
    return parsed


def programme_matches(identifier, framework):
    match = PROGRAMME_ID.fullmatch(identifier)
    return bool(match) and PROGRAMMES.get(match.group(1)) == framework


def _scan(client, now, profile, frameworks, pages_budget):
    """One full pass over the result pages; records are keyed by stable topic identity."""
    records, seen, kept, outside, total = {}, set(), {}, set(), None
    pages = rows_read = duplicates = 0
    for page in range(1, pages_budget + 1):
        response = client.funding_search(page, profile)
        if response.status != 200 or response.url != request_spec(page, profile)[0]:
            raise FetchError("Funding search response identity changed")
        try:
            data = json.loads(response.body)
            rows, count = data["results"], data["totalResults"]
            valid = (
                isinstance(rows, list)
                and type(count) is int
                and 0 <= count <= pages_budget * 50
                and type(data["pageNumber"]) is int
                and data["pageNumber"] == page
                and type(data["pageSize"]) is int
                and data["pageSize"] == 50
                and data.get("warnings") == []
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise FetchError("Funding search invalid response schema") from exc
        if not valid or (total is not None and count != total):
            raise FetchError("Funding search budget, warnings or count changed")
        total = count
        if len(rows) != min(50, max(0, total - (page - 1) * 50)):
            raise FetchError("Funding search page is incomplete")
        pages += 1
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("metadata"), dict):
                raise FetchError("Funding search record schema changed")
            meta = row["metadata"]
            identifier, language = single(meta, "identifier"), single(meta, "language")
            kind, status = single(meta, "type"), single(meta, "status")
            url, title = single(meta, "url"), single(meta, "title")
            framework = single(meta, "frameworkProgramme")
            rows_read += 1
            if (
                language != "en"
                or row.get("language") != "en"
                or framework not in frameworks
                or (profile == "programmes" and not programme_matches(identifier, framework))
                or kind not in {"1", "2", "8"}
                or status not in {"31094501", "31094502"}
                or row.get("url") != url
            ):
                raise FetchError("Funding search identity, scope or language changed")
            reference = None
            if PROFILES[profile]["tolerant"]:
                # Rows outside clusters 5-6 are only counted; they are never stored.
                if profile == "horizon" and not HORIZON_PROGRAMME_ID.fullmatch(identifier):
                    raise FetchError("Funding search identity, scope or language changed")
                if not IN_SCOPE[profile].match(identifier):
                    outside.add(identifier)
                    continue
                if kind == "8":
                    # Partnership calls reuse one topic identifier for separate calls.
                    reference = row.get("reference")
                    if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
                        raise FetchError("Funding search competitive call reference changed")
                url_ok = url in (
                    DETAIL + identifier + ".json",
                    PORTAL + "topic-details/" + identifier,
                ) or (
                    kind == "8"
                    and (COMPETITIVE.fullmatch(url) or PORTAL_COMPETITIVE.fullmatch(url))
                )
                key = (identifier, reference)
                canonical = json.dumps(meta, sort_keys=True)
                if not IDENTIFIERS[profile].fullmatch(identifier) or not url_ok:
                    raise FetchError("Funding search identity, scope or language changed")
                if key in kept:
                    # The index lists some topics twice; only byte-identical copies collapse.
                    if kept[key] != canonical:
                        raise FetchError("Funding search duplicate topic differs")
                    duplicates += 1
                    continue
                kept[key] = canonical
            elif (
                not IDENTIFIERS[profile].fullmatch(identifier)
                or identifier in seen
                or not (
                    url == DETAIL + identifier + ".json"
                    # LIFE also uses the reviewed portal path for type8 calls.
                    or (
                        kind == "8"
                        and (COMPETITIVE.fullmatch(url) or PORTAL_COMPETITIVE.fullmatch(url))
                    )
                )
            ):
                raise FetchError("Funding search identity, scope or language changed")
            seen.add(identifier)
            if not IN_SCOPE[profile].match(identifier):
                outside.add(identifier)
                continue
            deadlines, starts = meta.get("deadlineDate"), meta.get("startDate")
            parsed_deadlines = source_dates(deadlines)
            source_dates(starts)
            future = [
                raw for raw, date in zip(deadlines, parsed_deadlines, strict=True) if date > now
            ]
            elapsed_count = len(deadlines) - len(future)
            temporal_state = (
                "all_deadlines_elapsed"
                if not future
                else "mixed_deadlines_require_stage_review"
                if elapsed_count
                else "future_deadlines_require_opening_review"
            )
            records[(identifier, reference)] = {
                "identifier": identifier,
                **({"source_reference": reference} if reference else {}),
                "title": title,
                "source_url": url,
                "source_status_raw": status,
                "source_type_raw": kind,
                "deadline_dates_raw": deadlines,
                "start_dates_raw": starts,
                "all_source_deadlines_elapsed": not future,
                "deadline_time_evidence": {
                    "state": temporal_state,
                    "elapsed_count": elapsed_count,
                    "future_count": len(future),
                    "next_source_deadline_raw": min(future, key=datetime.fromisoformat)
                    if future
                    else None,
                    "new_applicant_eligibility_verified": False,
                },
            }
        if rows_read == total:
            break
    if rows_read != total:
        raise FetchError("Funding search incomplete batch")
    return {
        "records": records,
        "outside": outside,
        "total": total,
        "pages": pages,
        "duplicates": duplicates,
    }


def discover(client, now=None, profile="life"):
    if profile not in PROFILES:
        raise FetchError("Funding search profile outside reviewed scope")
    frameworks, pages_budget = PROFILES[profile]["frameworks"], PROFILES[profile]["pages"]
    if not frameworks:
        raise FetchError("Funding search profile has no confirmed programme")
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise FetchError("Funding search report time lacks timezone")
    union, outside, total, pages, duplicates, passes = {}, set(), None, 0, 0, 0
    # The SEDIA index orders equal-score rows differently per request, so a multi-page
    # result can shift rows between pages (live 2026-10-08: 254/244/247 topics from one
    # constant total). Repeat full passes until one adds no new topic; never store a
    # set that did not converge within the budget.
    for _ in range(MAX_PASSES):
        scan = _scan(client, now, profile, frameworks, pages_budget)
        passes += 1
        if total is not None and scan["total"] != total:
            raise FetchError("Funding search budget, warnings or count changed")
        total, pages, duplicates = (
            scan["total"],
            pages + scan["pages"],
            duplicates + scan["duplicates"],
        )
        new = scan["records"].keys() - union.keys()
        for key, record in scan["records"].items():
            if key in union and union[key] != record:
                raise FetchError("Funding search duplicate topic differs")
            union[key] = record
        outside |= scan["outside"]
        # A single page cannot shift; otherwise a converged pass adds nothing new.
        if total <= PAGE_SIZE or (passes > 1 and not new):
            break
    else:
        raise FetchError("Funding search paging unstable")
    records = list(union.values())
    elapsed = sum(row["all_source_deadlines_elapsed"] for row in records)
    return {
        "as_of": now.isoformat(),
        "scope": SCOPE[profile],
        "pages": pages,
        "passes": passes,
        "records": sorted(
            records, key=lambda row: (row["identifier"], row.get("source_reference", ""))
        ),
        "total": total,
        "outside_scope_count": len(outside),
        "duplicate_rows_dropped": duplicates,
        "all_deadlines_elapsed_count": elapsed,
        "mixed_deadline_records_count": sum(
            row["deadline_time_evidence"]["state"] == "mixed_deadlines_require_stage_review"
            for row in records
        ),
        "future_deadline_records_count": len(records) - elapsed,
        "quality": "all_source_deadlines_elapsed"
        if records and elapsed == len(records)
        else "review_required",
        "current_call_verified": False,
        "database_writes": False,
        "attachments_downloaded": False,
    }
