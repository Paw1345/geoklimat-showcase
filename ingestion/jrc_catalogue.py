"""Bounded English ENVI dataset metadata from the documented JRC SPARQL GET API."""

import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import urlencode

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

URL = "https://data.jrc.ec.europa.eu/sparql"
THEME = "http://publications.europa.eu/resource/authority/data-theme/ENVI"
CONFIG = {"mode": "jrc_environment", "theme": "ENVI", "language": "en"}
SLUG = "registry-181-jrc-environment"
NOTE = (
    "Katalog metadanych JRC: dokładny temat ENVI (Environment), tytuły i opisy angielskie. "
    "Do5000 pozycji/50stron po100; bez plików, geometrii i pomiarów. Daty dct:modified "
    "pozostają surowymi metadanymi zbioru, nie datą publikacji ani aktualizacji katalogu. "
    "Zakres zbiorów może być globalny; nie jest to maska Polski/UE ani historia30dni."
)
DATASET = re.compile(r"http://data\.europa\.eu/89h/([A-Za-z0-9][A-Za-z0-9._-]{0,149})\Z")
XSD = "http://www.w3.org/2001/XMLSchema#"
FIELDS = ["dataset", "title", "description", "modified"]
PAGE_SIZE = 100
MAX_PAGES = 50


def literal(binding, *, required=False):
    if binding is None and not required:
        return ""
    if (
        not isinstance(binding, dict)
        or set(binding) != {"type", "xml:lang", "value"}
        or binding.get("type") != "literal"
        or binding.get("xml:lang") != "en"
        or not isinstance(binding.get("value"), str)
        or not binding["value"].strip()
        or len(binding["value"]) > 30000
    ):
        raise FetchError("JRC literal language or schema changed")
    return binding["value"]


def source_date(binding):
    if binding is None:
        return ""
    if (
        not isinstance(binding, dict)
        or set(binding) != {"type", "datatype", "value"}
        or binding.get("type") != "literal"
        or not isinstance(binding.get("value"), str)
        or len(binding["value"]) > 40
    ):
        raise FetchError("JRC source date schema changed")
    value = binding["value"]
    try:
        if binding["datatype"] == XSD + "date":
            valid = date.fromisoformat(value).isoformat() == value
        elif binding["datatype"] == XSD + "dateTime":
            valid = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value))
            datetime.fromisoformat(value)
        else:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        raise FetchError("JRC source date invalid")
    return value


def fetch_catalogue(source, client):
    if (source.adapter, source.url, source.config) != ("json", URL, CONFIG):
        raise FetchError("Unreviewed JRC scope")
    entries, seen, previous = [], set(), None
    for page in range(MAX_PAGES):
        query = f"""PREFIX dcat: <http://www.w3.org/ns/dcat#>
PREFIX dct: <http://purl.org/dc/terms/>
SELECT DISTINCT ?dataset ?title ?description ?modified WHERE {{
 ?dataset a dcat:Dataset; dcat:theme <{THEME}>; dct:title ?title .
 FILTER(lang(?title)="en")
 OPTIONAL {{ ?dataset dct:description ?description . FILTER(lang(?description)="en") }}
 OPTIONAL {{ ?dataset dct:modified ?modified }}
}} ORDER BY ?dataset ?title ?description ?modified LIMIT {PAGE_SIZE} OFFSET {page * PAGE_SIZE}"""
        url = URL + "?" + urlencode({"query": query, "format": "application/sparql-results+json"})
        response = client.get(url, headers={"Accept": "application/sparql-results+json"})
        if response.status != 200 or response.url != url:
            raise FetchError("JRC left reviewed endpoint or unexpected status")
        try:
            data = json.loads(response.body)
            rows = data["results"]["bindings"]
            valid = data["head"]["vars"] == FIELDS and isinstance(rows, list)
        except (ValueError, KeyError, TypeError) as exc:
            raise FetchError("JRC JSON schema changed") from exc
        if not valid or len(rows) > PAGE_SIZE:
            raise FetchError("JRC JSON schema or page budget changed")
        for row in rows:
            if not isinstance(row, dict) or not {"dataset", "title"} <= set(row) <= set(FIELDS):
                raise FetchError("JRC binding schema changed")
            dataset = row["dataset"]
            if (
                not isinstance(dataset, dict)
                or set(dataset) != {"type", "value"}
                or dataset.get("type") != "uri"
                or not isinstance(dataset.get("value"), str)
            ):
                raise FetchError("JRC dataset identity changed")
            uri = dataset["value"]
            match = DATASET.fullmatch(uri)
            if match is None or uri in seen or (previous is not None and uri <= previous):
                raise FetchError("JRC identity, duplicates or ordering changed")
            title = literal(row["title"], required=True)
            description = literal(row.get("description"))
            modified = source_date(row.get("modified"))
            entry = safe_entry(
                Entry(
                    uri,
                    title,
                    f"https://data.jrc.ec.europa.eu/dataset/{match[1]}",
                    description,
                    metadata={
                        "record_kind": "dataset_collection",
                        "dataset_uri": uri,
                        "catalogue_url": URL,
                        "theme_uri": THEME,
                        "language": "en",
                        "dataset_modified_raw": modified,
                        "dataset_modified_predicate": "http://purl.org/dc/terms/modified",
                        "resources_downloaded": False,
                        "listing_scope": "english_ENVI_catalogue_not_observations",
                        "document_hash": hashlib.sha256(
                            json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
                        ).hexdigest(),
                    },
                ),
                URL,
            )
            if entry is None:
                raise FetchError("Invalid JRC entry")
            entries.append(entry)
            seen.add(uri)
            previous = uri
        if len(rows) < PAGE_SIZE:
            digest = hashlib.sha256("".join(e.digest() for e in entries).encode()).hexdigest()
            return entries, 0, response, digest
    raise FetchError("JRC page budget exceeded; no partial batch committed")
