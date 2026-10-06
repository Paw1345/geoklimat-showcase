"""Bounded ELI catalogue pagination; metadata only, no act/PDF requests."""

import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import urlencode

from django.utils import timezone

from ingestion.adapters import Entry
from ingestion.transport import FetchError

PAGE_SIZE = 250
MAX_PAGES = 40
MAX_BYTES = 20 * 1024 * 1024


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def decode(body):
    try:
        data = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        rows = data["items"]
        count = data.get("count", len(rows))
        total = data.get("totalCount", count)
        offset = data.get("offset", 0)
        if (
            not isinstance(rows, list)
            or any(type(value) is not int or value < 0 for value in (count, total, offset))
            or count != len(rows)
            or offset + count > total
            or count > 10000
        ):
            raise ValueError
        return data, rows, total, offset
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise FetchError("Invalid ELI catalogue envelope") from exc


def parse_rows(rows, journal=None, year=None):
    entries, seen = [], set()
    try:
        for row in rows:
            identifier = row["ELI"]
            match = re.fullmatch(r"(DU|MP)/([12][0-9]{3})/([1-9][0-9]*)", identifier)
            if not match or identifier in seen:
                raise ValueError
            publisher, source_year, position = match.groups()
            if journal is not None and (publisher, int(source_year)) != (journal, year):
                raise ValueError
            for field, expected in (
                ("publisher", publisher),
                ("year", int(source_year)),
                ("pos", int(position)),
            ):
                if field in row and (
                    type(row[field]) is not type(expected) or row[field] != expected
                ):
                    raise ValueError
            title = row["title"]
            if not isinstance(title, str) or not title.strip() or len(title) > 1000:
                raise ValueError
            for field in ("promulgation", "announcementDate", "entryIntoForce"):
                value = row.get(field, "")
                if value not in (None, ""):
                    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                        raise ValueError
            changed = row.get("changeDate", "")
            updated = ""
            if changed:
                if not isinstance(changed, str) or "T" not in changed:
                    raise ValueError
                stamp = datetime.fromisoformat(changed)
                # Source examples have no UTC offset. Preserve raw time, do not invent UTC.
                if stamp.tzinfo is not None:
                    updated = changed
            for field in ("displayAddress", "status", "type", "inForce"):
                if field in row and (not isinstance(row[field], str) or len(row[field]) > 600):
                    raise ValueError
            metadata = {
                key: row[key]
                for key in (
                    "ELI",
                    "status",
                    "type",
                    "announcementDate",
                    "entryIntoForce",
                    "inForce",
                )
                if key in row
            }
            metadata.update(
                {
                    "source_change_date_raw": changed,
                    "source_change_offset_present": bool(updated),
                    "current_legal_status_verified": False,
                    "catalogue_only": True,
                }
            )
            entries.append(
                Entry(
                    identifier,
                    title,
                    f"https://eli.gov.pl/eli/{identifier}/ogl",
                    row.get("displayAddress", ""),
                    row.get("promulgation", ""),
                    updated,
                    metadata,
                )
            )
            seen.add(identifier)
        return entries
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FetchError("Invalid ELI catalogue row; no partial batch") from exc


def fetch_catalogue(source, client):
    match = re.fullmatch(r"https://api\.sejm\.gov\.pl/eli/acts/(DU|MP)/\{year\}", source.url)
    if source.adapter != "eli" or not match:
        raise FetchError("Unknown ELI paged catalogue profile")
    journal, current = match.group(1), timezone.now().year
    entries, seen, fingerprints, size = [], set(), [], 0
    for year in (current, current - 1):
        offset, total = 0, None
        for _ in range(MAX_PAGES):
            url = "https://api.sejm.gov.pl/eli/acts/search?" + urlencode(
                {
                    "publisher": journal,
                    "year": year,
                    "limit": PAGE_SIZE,
                    "offset": offset,
                    "sortBy": "position",
                    "sortDir": "asc",
                }
            )
            response = client.get(url)
            size += len(response.body)
            if response.status != 200 or response.url != url or size > MAX_BYTES:
                raise FetchError("ELI response or byte budget mismatch")
            _, rows, received_total, received_offset = decode(response.body)
            if (
                received_offset != offset
                or len(rows) > PAGE_SIZE
                or (total is not None and received_total != total)
                or received_total > MAX_PAGES * PAGE_SIZE
            ):
                raise FetchError("ELI pagination count changed or budget exceeded")
            total = received_total
            batch = parse_rows(rows, journal, year)
            positions = [int(entry.key.rsplit("/", 1)[1]) for entry in batch]
            previous = int(entries[-1].key.rsplit("/", 1)[1]) if offset else 0
            if positions != sorted(positions) or (positions and positions[0] <= previous):
                raise FetchError("ELI pagination order mismatch")
            for entry in batch:
                if entry.key in seen:
                    raise FetchError("Duplicate ELI across catalogue pages")
                seen.add(entry.key)
            entries.extend(batch)
            fingerprints.append(hashlib.sha256(response.body).hexdigest())
            offset += len(rows)
            if offset == total:
                break
            if not rows:
                raise FetchError("ELI pagination stopped before total")
        else:
            raise FetchError("ELI pagination page budget exceeded")
    from ingestion.adapters import safe_entry

    safe = [safe_entry(entry, response.url) for entry in entries]
    if any(entry is None for entry in safe):
        raise FetchError("Unsafe ELI catalogue entry")
    return safe, 0, response, hashlib.sha256("".join(fingerprints).encode()).hexdigest()
