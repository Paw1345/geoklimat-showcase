"""Read-only bounded LIFE search quality report; no automatic funding/deadline facts."""

import json
import re
from datetime import datetime

from django.utils import timezone

from ingestion.funding_search_transport import request_spec
from ingestion.transport import FetchError

IDENTIFIER = re.compile(r"LIFE-[A-Za-z0-9-]{1,180}\Z")
DETAIL = "https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/"
COMPETITIVE = re.compile(re.escape(DETAIL) + r"[0-9]{1,12}COMPETITIVE_CALLen\Z")


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


def discover(client, now=None):
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise FetchError("Funding search report time lacks timezone")
    records, seen, total = [], set(), None
    pages = 0
    for page in range(1, 6):
        response = client.funding_search(page)
        if response.status != 200 or response.url != request_spec(page)[0]:
            raise FetchError("Funding search response identity changed")
        try:
            data = json.loads(response.body)
            rows, count = data["results"], data["totalResults"]
            valid = (
                isinstance(rows, list)
                and type(count) is int
                and 0 <= count <= 250
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
            if (
                not IDENTIFIER.fullmatch(identifier)
                or identifier in seen
                or language != "en"
                or row.get("language") != "en"
                or single(meta, "frameworkProgramme") != "43252405"
                or kind not in {"1", "2", "8"}
                or status not in {"31094501", "31094502"}
                or not (
                    url == DETAIL + identifier + ".json"
                    or (kind == "8" and COMPETITIVE.fullmatch(url))
                )
                or row.get("url") != url
            ):
                raise FetchError("Funding search identity, scope or language changed")
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
            seen.add(identifier)
            records.append(
                {
                    "identifier": identifier,
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
            )
        if len(records) == total:
            break
    if len(records) != total:
        raise FetchError("Funding search incomplete batch")
    elapsed = sum(row["all_source_deadlines_elapsed"] for row in records)
    return {
        "as_of": now.isoformat(),
        "scope": "LIFE2021-2027/en/status_codes31094501,31094502",
        "pages": pages,
        "records": sorted(records, key=lambda row: row["identifier"]),
        "total": total,
        "all_deadlines_elapsed_count": elapsed,
        "mixed_deadline_records_count": sum(
            row["deadline_time_evidence"]["state"] == "mixed_deadlines_require_stage_review"
            for row in records
        ),
        "future_deadline_records_count": total - elapsed,
        "quality": "all_source_deadlines_elapsed"
        if total and elapsed == total
        else "review_required",
        "current_call_verified": False,
        "database_writes": False,
        "attachments_downloaded": False,
    }
