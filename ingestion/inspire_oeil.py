"""One reviewed INSPIRE procedure; public metadata, no people or child fetches."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

REFERENCE = "2025/0393(COD)"
TITLE = (
    "Simplification of certain requirements for spatial information infrastructure "
    "in the Union (INSPIRE) (Omnibus VIII on environmental legislation)"
)
URL = "https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference=2025%2F0393%28COD%29"
CONFIG = {"strict_endpoint": True, "inspire_oeil": REFERENCE}
SLUG = "registry-168-oeil-inspire"
LEGACY_URL = "https://eur-lex.europa.eu/procedure/EN/2025_393"
NOTE = (
    "OEIL Parlamentu Europejskiego: status, wydarzenia i prognozy wyłącznie "
    "procedury2025/0393(COD) INSPIRE. Migawka metadanych, bez osób, dokumentów, "
    "załączników i archiwum. Daty zdarzeń/prognoz nie są datami publikacji "
    "ani wejścia prawa w życie. Nie zastępuje pełnego zakresu EUR-Lex."
)


def one(node, selector):
    matches = node.select(selector)
    if len(matches) != 1:
        raise FetchError("INSPIRE OEIL field missing or ambiguous")
    return matches[0]


def section(main, identifier, heading):
    node = one(main, "#" + identifier)
    if text(one(node, "h2")) != heading:
        raise FetchError("INSPIRE OEIL section changed")
    return node


def dated_rows(node, headers, forecast=False):
    table = one(node, "table")
    if [text(c) for c in table.select("thead th")] != headers:
        raise FetchError("INSPIRE OEIL columns changed")
    rows = one(table, "tbody").find_all("tr", recursive=False)
    if len(rows) > 100 or (not forecast and not rows):
        raise FetchError("INSPIRE OEIL event budget changed")
    result, seen = [], set()
    for row in rows:
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(headers) or any(c.get("colspan") or c.get("rowspan") for c in cells):
            raise FetchError("INSPIRE OEIL event schema changed")
        raw_day, label = text(cells[0]), text(cells[1])
        if not re.fullmatch(r"\d{2}/\d{2}/20\d{2}", raw_day) or not label or len(label) > 500:
            raise FetchError("INSPIRE OEIL event fields invalid")
        try:
            day = datetime.strptime(raw_day, "%d/%m/%Y").date().isoformat()
        except ValueError as exc:
            raise FetchError("INSPIRE OEIL event date invalid") from exc
        reference = "" if forecast else text(cells[2])
        if len(reference) > 200:
            raise FetchError("INSPIRE OEIL reference budget changed")
        identity = (day, label, reference)
        if identity in seen:
            raise FetchError("INSPIRE OEIL event duplicated")
        seen.add(identity)
        result.append(
            {
                "date": day,
                "source_date_text": raw_day,
                "label": label,
                "reference": reference,
                "date_kind": "forecast" if forecast else "event",
            }
        )
    return result


def parse_procedure(source, response):
    if (
        (source.adapter, source.url, source.config) != ("web", URL, CONFIG)
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("INSPIRE OEIL endpoint identity changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("INSPIRE OEIL UTF-8 changed") from exc
    if text(one(soup, "head > title")) != (
        "Procedure File: " + REFERENCE + " | Legislative Observatory | European Parliament"
    ):
        raise FetchError("INSPIRE OEIL page identity changed")
    main = one(soup, "main#website-body")
    if text(one(main, "h2.es_title-h1")) != REFERENCE:
        raise FetchError("INSPIRE OEIL procedure reference changed")
    titles = [h for h in main.select("h2.es_title-h2") if "INSPIRE" in text(h)]
    if len(titles) != 1 or text(titles[0]) != TITLE:
        raise FetchError("INSPIRE OEIL procedure subject changed")
    basic = section(main, "section1", "Basic information")
    status = text(one(basic, "p.text-danger"))
    if not status or len(status) > 300:
        raise FetchError("INSPIRE OEIL source status missing")
    technical = section(main, "section5", "Technical information")
    fields = {}
    technical_rows = one(one(technical, "table"), "tbody").find_all("tr", recursive=False)
    if not 1 <= len(technical_rows) <= 30:
        raise FetchError("INSPIRE OEIL technical budget changed")
    for row in technical_rows:
        key, value = text(one(row, "th")), text(one(row, "td"))
        if key in fields or len(key) > 100 or len(value) > 500:
            raise FetchError("INSPIRE OEIL technical fields changed")
        fields[key] = value
    if (
        fields.get("Procedure reference") != REFERENCE
        or fields.get("Stage reached in procedure") != status
        or fields.get("Legislative instrument") != "Directive"
        or fields.get("Amendments and repeals") != "Amending Directive 2007/2 2004/0175(COD)"
    ):
        raise FetchError("INSPIRE OEIL reference, status or amending act mismatch")
    events = dated_rows(
        section(main, "section3", "Key events"), ["Date", "Event", "Reference", "Summary"]
    )
    forecasts = dated_rows(
        section(main, "section4", "Forecasts"), ["Date", "Subject"], forecast=True
    )
    return [
        Entry(
            "oeil:" + REFERENCE,
            TITLE,
            URL,
            "Status źródłowy OEIL: " + status,
            metadata={
                "record_kind": "procedure_snapshot",
                "procedure_reference": REFERENCE,
                "procedure_status_text": status,
                "source_events": events,
                "source_forecasts": forecasts,
                "listing_scope": "status_events_forecasts",
                "full_document_downloaded": False,
                "eurlex_reference_url": LEGACY_URL,
            },
        )
    ]
