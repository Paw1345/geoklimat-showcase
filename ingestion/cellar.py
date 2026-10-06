"""Cellar document metadata: exact EuroVoc2470, English titles, document-date window."""

import json
import re
from datetime import date, timedelta
from urllib.parse import urlencode

from django.utils import timezone

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

URL = "https://publications.europa.eu/webapi/rdf/sparql"
CONFIG = {"mode": "cellar_metadata", "eurovoc": "2470", "language": "en"}
NOTE = (
    "Metadane Cellar: dokładny EuroVoc2470 (environmental policy), tytuły angielskie, "
    "30 dni według daty dokumentu UTC. Nie jest to data publikacji/aktualizacji ani "
    "pełne pokrycie klimatu/INSPIRE/GIS. Maksymalnie500 rekordów; bez pobierania dokumentów."
)
XSD_DATE = "http://www.w3.org/2001/XMLSchema#date"
WORK = re.compile(
    r"http://publications\.europa\.eu/resource/cellar/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z"
)


def fetch_metadata(source, client):
    if (source.adapter, source.url, source.config) != ("json", URL, CONFIG):
        raise FetchError("Unreviewed Cellar scope")
    today = timezone.now().date()
    cutoff = today - timedelta(days=29)
    entries, seen, previous = [], set(), None
    for page in range(10):
        query = f'''PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT DISTINCT ?work ?date ?title WHERE {{
 ?work cdm:work_is_about_concept_eurovoc <http://eurovoc.europa.eu/2470>;
       cdm:work_date_document ?date .
 ?e cdm:expression_belongs_to_work ?work;
    cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG>;
    cdm:expression_title ?title .
 FILTER(?date >= "{cutoff}"^^<{XSD_DATE}> && ?date <= "{today}"^^<{XSD_DATE}>)
}} ORDER BY ?work ?date ?title LIMIT 50 OFFSET {page * 50}'''
        request_url = (
            URL + "?" + urlencode({"query": query, "format": "application/sparql-results+json"})
        )
        response = client.get(request_url, headers={"Accept": "application/sparql-results+json"})
        if response.url != request_url:
            raise FetchError("Cellar left reviewed endpoint")
        try:
            data = json.loads(response.body)
            rows = data["results"]["bindings"]
            valid = data["head"]["vars"] == ["work", "date", "title"] and isinstance(rows, list)
        except (ValueError, KeyError, TypeError) as exc:
            raise FetchError("Cellar JSON schema changed") from exc
        if not valid or len(rows) > 50:
            raise FetchError("Cellar JSON schema or page budget changed")
        for row in rows:
            try:
                work, day, title = row["work"], row["date"], row["title"]
                match = WORK.fullmatch(work["value"])
                document_day = date.fromisoformat(day["value"])
                order = (work["value"], day["value"], title["value"])
                valid = (
                    set(row) == {"work", "date", "title"}
                    and work["type"] == "uri"
                    and match is not None
                    and day["type"] == "literal"
                    and day["datatype"] == XSD_DATE
                    and document_day.isoformat() == day["value"]
                    and cutoff <= document_day <= today
                    and title["type"] == "literal"
                    and title["xml:lang"] == "en"
                    and isinstance(title["value"], str)
                    and bool(title["value"].strip())
                    and work["value"] not in seen
                    and (previous is None or order > previous)
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise FetchError("Invalid Cellar binding") from exc
            if not valid:
                raise FetchError("Cellar identity, date, language or ordering changed")
            seen.add(work["value"])
            previous = order
            entry = safe_entry(
                Entry(
                    work["value"],
                    title["value"],
                    f"https://publications.europa.eu/resource/cellar/{match[1]}",
                    f"Cellar · EuroVoc2470: environmental policy. Data dokumentu: {day['value']}.",
                    metadata={
                        "record_kind": "publication_metadata",
                        "cellar_uri": work["value"],
                        "document_date": day["value"],
                        "document_date_predicate": "work_date_document",
                        "eurovoc": ["2470"],
                        "language": "en",
                        "resources_downloaded": False,
                        "listing_scope": "document_date_30_calendar_days",
                    },
                ),
                URL,
            )
            if entry is None:
                raise FetchError("Invalid Cellar entry")
            entries.append(entry)
        if len(rows) < 50:
            return entries, 0, response
    raise FetchError("Cellar page budget exceeded; no partial batch committed")
