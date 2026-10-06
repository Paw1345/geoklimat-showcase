"""Public Have Your Say initiative metadata; never feedback, accounts or attachments."""

import hashlib
import json
import math
import re
from datetime import datetime
from urllib.parse import urlencode

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

BASE = "https://ec.europa.eu/info/law/better-regulation/brpapi"
URL = BASE + "/searchInitiatives"
CONFIG = {"mode": "hys_environment"}
LEGACY = "https://ec.europa.eu/info/law/better-regulation/have-your-say_en"
NOTE = (
    "Have Your Say: katalog inicjatyw ENV i CLIMA; statusy i terminy jako tekst źródła, "
    "bez potwierdzenia otwarcia konsultacji, opinii użytkowników i plików."
)
THEMES = ("ENV", "CLIMA")
SIZE, MAX_PAGES, MAX_BYTES = 100, 10, 10 * 1024 * 1024


def pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def string(value, maximum=1000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError
    return value


def entry(row, theme):
    identity = row["id"]
    if (
        isinstance(identity, bool)
        or not isinstance(identity, (int, float))
        or (not math.isfinite(identity) or int(identity) != identity or not 1 <= identity <= 10**9)
    ):
        raise ValueError
    topics = row["topics"]
    if not isinstance(topics, list) or not 1 <= len(topics) <= 50:
        raise ValueError
    topic_map = {}
    for topic in topics:
        code = string(topic["code"], 40)
        if not re.fullmatch(r"[A-Z_]+", code) or code in topic_map:
            raise ValueError
        topic_map[code] = string(topic["label"], 300)
    if theme not in topic_map:
        raise ValueError
    translations = row.get("initiativeTranslations", [])
    if not isinstance(translations, list) or len(translations) > 100:
        raise ValueError
    titles, seen = {}, set()
    for translation in translations:
        language = string(translation["language"], 5)
        field = string(translation["field"], 40)
        value = string(translation["value"], 5000, empty=True)
        key = language, field
        if key in seen:
            raise ValueError
        seen.add(key)
        if field == "SHORT_TITLE" and language in {"PL", "EN"}:
            titles[language] = string(value)
    original = string(row["shortTitle"])
    language = "PL" if titles.get("PL") else "EN" if titles.get("EN") else "source"
    title = titles.get(language, original)
    stages = row.get("currentStatuses", [])
    if not isinstance(stages, list) or len(stages) > 30:
        raise ValueError
    statuses = []
    for stage in stages:
        if type(stage["isCurrent"]) is not bool:
            raise ValueError
        result = {"isCurrent": stage["isCurrent"]}
        for name in ("frontEndStage", "receivingFeedbackStatus"):
            result[name] = string(stage[name], 100)
        for name in ("feedbackStartDate", "feedbackEndDate"):
            raw = stage.get(name)
            if raw not in (None, ""):
                string(raw, 30)
                stamp = datetime.strptime(raw, "%Y/%m/%d %H:%M:%S")
                if stamp.strftime("%Y/%m/%d %H:%M:%S") != raw:
                    raise ValueError
            result[name] = raw
        statuses.append(result)
    metadata = {
        "record_kind": "policy_catalogue_metadata",
        "listing_scope": "hys_env_clima_catalogue",
        "initiative_id": int(identity),
        "reference": (
            string(row["reference"], 200, empty=True) if row.get("reference") is not None else None
        ),
        "initiative_status_raw": string(row["initiativeStatus"], 100),
        "foreseen_act_type_raw": (
            string(row["foreseenActType"], 100, empty=True)
            if row.get("foreseenActType") is not None
            else None
        ),
        "source_title": original,
        "title_pl": titles.get("PL", ""),
        "title_en": titles.get("EN", ""),
        "display_language": language,
        "source_topics": [{"code": code, "label": topic_map[code]} for code in sorted(topic_map)],
        "current_statuses_raw": statuses,
        "current_statuses_field_present": "currentStatuses" in row,
        "feedback_dates_timezone_verified": False,
        "consultation_open_verified": False,
        "legal_status_verified": False,
        "full_history_verified": False,
    }
    return Entry(
        key="HYS:" + str(int(identity)),
        title=title,
        url=BASE + "/groupInitiatives/" + str(int(identity)),
        metadata=metadata,
    )


def fetch_catalogue(source, client):
    if (source.adapter, source.url, source.config) != ("json", URL, CONFIG):
        raise FetchError("Unknown Have Your Say catalogue profile")
    entries, hashes, used = {}, [], 0
    try:
        for theme in THEMES:
            total, identities = None, set()
            for number in range(MAX_PAGES):
                url = (
                    URL
                    + "?"
                    + urlencode(
                        {
                            "topic": theme,
                            "page": number,
                            "size": SIZE,
                            "language": "EN",
                        }
                    )
                )
                response = client.get(url)
                used += len(response.body)
                if response.status != 200 or response.url != url or used > MAX_BYTES:
                    raise ValueError
                data = json.loads(
                    response.body.decode("utf-8"),
                    object_pairs_hook=pairs,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                )
                page = data["initiativeResultDtoPage"]
                rows = page["content"]
                if data["exactMatch"] is not True or not isinstance(rows, list):
                    raise ValueError
                for key in ("number", "size", "totalElements", "totalPages", "numberOfElements"):
                    if type(page[key]) is not int or page[key] < 0:
                        raise ValueError
                expected_total = page["totalElements"]
                pages = max(1, math.ceil(expected_total / SIZE))
                if (
                    page["number"] != number
                    or page["size"] != SIZE
                    or pages > MAX_PAGES
                    or page["totalPages"] not in ({pages} if expected_total else {0, 1})
                    or page["numberOfElements"] != len(rows)
                    or len(rows) != min(SIZE, max(0, expected_total - number * SIZE))
                    or (total is not None and total != expected_total)
                    or page["last"] is not (number == pages - 1)
                    or page["first"] is not (number == 0)
                ):
                    raise ValueError
                total = expected_total
                for row in rows:
                    parsed = entry(row, theme)
                    if parsed.key in identities:
                        raise ValueError
                    identities.add(parsed.key)
                    if parsed.key in entries and entries[parsed.key].digest() != parsed.digest():
                        raise ValueError
                    entries[parsed.key] = parsed
                hashes.append(hashlib.sha256(response.body).hexdigest())
                if page["last"]:
                    if len(identities) != total:
                        raise ValueError
                    break
            else:
                raise ValueError
    except (ValueError, UnicodeError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise FetchError("Have Your Say schema/pagination changed; no partial catalogue") from exc
    result = [safe_entry(value, URL) for _, value in sorted(entries.items())]
    if any(value is None for value in result):
        raise FetchError("Unsafe Have Your Say entry")
    return result, 0, response, hashlib.sha256("".join(hashes).encode()).hexdigest()
