"""Official Eurostat catalogue TOC: environment subtree, metadata only."""

import csv
import hashlib
import io
import json
import re

from ingestion.adapters import Entry, date_value, safe_entry
from ingestion.transport import FetchError

URL = "https://ec.europa.eu/eurostat/api/dissemination/catalogue/toc/txt?lang=en"
CONFIG = {"mode": "eurostat_toc", "roots": ["env"]}
NOTE = (
    "Katalog Eurostat: wszystkie tabele i zbiory gałęzi Environment (env), "
    "monitoring zmian danych i struktury. Metadane, bez automatycznego pobierania wartości."
)
HEADER = [
    "title",
    "code",
    "type",
    "last update of data",
    "last table structure change",
    "data start",
    "data end",
    "values",
]


def fetch_catalogue(source, client):
    if source.url != URL or source.config != CONFIG:
        raise FetchError("Unreviewed Eurostat catalogue scope")
    response = client.get(URL)
    if response.url != URL:
        raise FetchError("Eurostat catalogue left reviewed endpoint")
    body = response.body.decode("utf-8-sig")
    if not body.endswith("\n"):
        raise FetchError("Truncated Eurostat catalogue")
    reader = csv.reader(io.StringIO(body), delimiter="\t", strict=True)
    if next(reader, None) != HEADER:
        raise FetchError("Eurostat TOC header changed")
    stack, found, entries, identities = [], set(), [], {}
    for count, row in enumerate(reader, 1):
        if count > 25000 or len(row) != len(HEADER):
            raise FetchError("Eurostat TOC row schema or budget exceeded")
        title, code, kind, updated, structure, first, last, values = row
        depth = len(title) - len(title.lstrip(" "))
        if depth % 4 or not re.fullmatch(r"[a-zA-Z0-9_.-]+", code):
            raise FetchError("Invalid Eurostat hierarchy or identity")
        depth //= 4
        stack = stack[:depth]
        if depth != len(stack) or kind not in {"folder", "dataset", "table"}:
            raise FetchError("Broken Eurostat hierarchy")
        stack.append(code)
        if kind == "folder":
            if code in CONFIG["roots"]:
                found.add(code)
            continue
        if not any(root in stack[:-1] for root in CONFIG["roots"]):
            continue
        dates = [
            date_value(value) for value in (updated.strip(), structure.strip()) if value.strip()
        ]
        if any(value is None for value in dates) or (
            values.strip() and not values.strip().isdigit()
        ):
            raise FetchError("Invalid Eurostat catalogue dates or count")
        stable = dict(zip(HEADER, [title.strip(), *row[1:]], strict=True))
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        if code in identities:
            if identities[code] != digest:
                raise FetchError("Conflicting duplicate Eurostat dataset")
            continue
        identities[code] = digest
        entry = safe_entry(
            Entry(
                code,
                title.strip(),
                f"https://ec.europa.eu/eurostat/databrowser/view/{code}/default/table?lang=en",
                f"Eurostat {code}. Dane: {first.strip()}–{last.strip()}. "
                f"Aktualizacja danych: {updated.strip() or 'brak'}; "
                f"zmiana struktury: {structure.strip() or 'brak'}. "
                f"Wartości: {values.strip() or 'brak'}.",
                updated=max(dates).isoformat() if dates else "",
                metadata={
                    "record_kind": "dataset_collection",
                    "dataset_code": code,
                    "data_updated": updated.strip(),
                    "structure_updated": structure.strip(),
                    "data_start": first.strip(),
                    "data_end": last.strip(),
                    "values": int(values) if values.strip() else None,
                    "resources_downloaded": False,
                    "document_hash": digest,
                },
            ),
            URL,
        )
        if entry is None:
            raise FetchError("Invalid Eurostat metadata")
        entries.append(entry)
    if found != set(CONFIG["roots"]) or not entries:
        raise FetchError("Eurostat environment subtree missing or empty")
    return entries, 0, response, hashlib.sha256(response.body).hexdigest()
