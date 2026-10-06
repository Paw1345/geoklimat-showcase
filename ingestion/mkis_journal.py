"""Fixed official Gov.pl journal register; metadata, not a full-act/legal opinion."""

import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from ingestion.adapters import Entry
from ingestion.transport import FetchError

URL = "https://www.gov.pl/api/data/registers/search?pageId=21328136"
PAGE = "https://www.gov.pl/web/klimat/dziennik-urzedowy-ministra-klimatu-i-srodowiska"
LEGACY = "https://www.gov.pl/web/klimat/dziennik-urzedowy"
CONFIG = {"strict_endpoint": True, "mkis_journal": "21328136"}
NOTE = (
    "Metadane bieżącego rejestru Dziennika Urzędowego MKiŚ na Gov.pl. "
    "Daty ogłoszenia, wydania i wejścia w życie rozdzielone; bez treści PDF "
    "i archiwum sprzed 1 lipca 2026. Nie potwierdza aktualnego stanu prawnego."
)


def source_day(raw, optional=False):
    if not isinstance(raw, str):
        raise FetchError("MKIS journal date schema changed")
    if optional and raw == "":
        return ""
    if not re.fullmatch(r"[0-9]{2}([.-])[0-9]{2}\1[0-9]{4}", raw):
        raise FetchError("MKIS journal source date format changed")
    try:
        return datetime.strptime(raw.replace("-", "."), "%d.%m.%Y").date().isoformat()
    except ValueError as exc:
        raise FetchError("MKIS journal source date invalid") from exc


def dictionary(raw):
    if not isinstance(raw, str):
        raise FetchError("MKIS journal dictionary schema changed")
    values = json.loads(raw)
    if not isinstance(values, list) or len(values) > 30:
        raise FetchError("MKIS journal dictionary budget changed")
    result = []
    for value in values:
        if (
            not isinstance(value, dict)
            or set(value) != {"id", "value"}
            or not isinstance(value["id"], str)
            or value["id"] != value["value"]
            or not 1 <= len(value["value"]) <= 200
            or value["value"] in result
        ):
            raise FetchError("MKIS journal dictionary identity changed")
        result.append(value["value"])
    return result


def parse_register(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("MKIS journal endpoint/profile changed")
    try:
        rows = json.loads(response.body.decode("utf-8"))
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise FetchError("MKIS journal register missing or budget changed")
        entries, ids, citations, urls = [], set(), set(), set()
        for row in rows:
            if not isinstance(row, dict):
                raise FetchError("MKIS journal row schema changed")
            identity, position = row["pageId"], row["Pozycja"].strip()
            title, path = row["Tytuł aktu"], row["pageUrl"]
            years = dictionary(row["Rok"])
            issuers = dictionary(row["Organ wydający"])
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[1-9][0-9]{0,11}", identity)
                or not re.fullmatch(r"\(Poz\. [1-9][0-9]{0,5}\)", position)
                or len(years) != 1
                or not re.fullmatch(r"20[0-9]{2}", years[0])
                or issuers != ["Minister Klimatu i Środowiska"]
                or not isinstance(title, str)
                or not title.startswith(position + " ")
                or len(title) > 1000
                or not isinstance(path, str)
                or len(path) > 1400
                or not re.fullmatch(r"/web/klimat/poz-[1-9][0-9]*-[a-z0-9%-]+", path)
                or urlsplit(path).query
                or urlsplit(path).fragment
                or identity in ids
                or (years[0], position) in citations
                or path in urls
            ):
                raise FetchError("MKIS journal record identity changed")
            number = re.search(r"[0-9]+", position)[0]
            if not path.startswith("/web/klimat/poz-" + number + "-"):
                raise FetchError("MKIS journal link/position conflict")
            published = source_day(row["Data ogłoszenia"])
            issued = source_day(row["Data wydania"])
            effective = source_day(row["Data wejścia w życie"], optional=True)
            if published[:4] != years[0] or issued > published:
                raise FetchError("MKIS journal year/date conflict")
            related = row["Akty powiązane"]
            if not isinstance(related, str) or len(related) > 3000:
                raise FetchError("MKIS journal related-act field changed")
            entries.append(
                Entry(
                    "mkis-journal:page:" + identity,
                    title,
                    "https://www.gov.pl" + path,
                    "",
                    published,
                    "",
                    {
                        "record_kind": "legal_register_metadata",
                        "listing_scope": "gov_pl_current_register",
                        "source_register_id": "21328136",
                        "source_record_id": identity,
                        "journal_year": years[0],
                        "journal_position": number,
                        "source_publication_date_text": row["Data ogłoszenia"],
                        "source_issue_date": issued,
                        "source_issue_date_text": row["Data wydania"],
                        "source_effective_date": effective,
                        "source_effective_date_text": row["Data wejścia w życie"],
                        "source_act_types": dictionary(row["Typ"]),
                        "source_index_terms": dictionary(row["Skorowidz"]),
                        "source_related_acts_text": related,
                        "full_document_downloaded": False,
                        "current_legal_status_verified": False,
                    },
                )
            )
            ids.add(identity)
            citations.add((years[0], position))
            urls.add(path)
        return entries
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FetchError("MKIS journal schema or encoding changed") from exc
