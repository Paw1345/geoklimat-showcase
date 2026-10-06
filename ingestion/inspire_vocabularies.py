"""INSPIRE ConceptScheme English labels from Cellar; no code values or inferred dates."""

import hashlib
import json
import re
from urllib.parse import urlencode

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

URL = "https://publications.europa.eu/webapi/rdf/sparql"
CONFIG = {"mode": "inspire_schemes", "language": "en", "limit": 1000}
SLUG = "registry-180-inspire-schemes"
EVIDENCE = "https://op.europa.eu/en/web/eu-vocabularies/inspire-registry"
NOTE = (
    "Katalog schematów INSPIRE (SKOS ConceptScheme): angielskie nazwy i dostępne opisy. "
    "Do1000schematów w jednej odpowiedzi, bez wartości kodowych, aktów, geometrii "
    "i dat publikacji. Zmiany opisów nie potwierdzają zmian wszystkich wartości rejestru. "
    "Pełne EU Vocabularies nadal open."
)
URI = re.compile(r"http://inspire\.ec\.europa\.eu/[A-Za-z0-9_/-]{1,300}\Z")
QUERY = """PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX dct: <http://purl.org/dc/terms/>
SELECT DISTINCT ?scheme ?label ?description WHERE {
 ?scheme a skos:ConceptScheme; skos:prefLabel ?label .
 FILTER(STRSTARTS(STR(?scheme), "http://inspire.ec.europa.eu/") && LANG(?label)="en")
 OPTIONAL { ?scheme dct:description ?description FILTER(LANG(?description)="en") }
} ORDER BY ?scheme ?label ?description LIMIT 1001"""


def literal(binding, limit):
    if (
        not isinstance(binding, dict)
        or set(binding) != {"type", "xml:lang", "value"}
        or binding["type"] != "literal"
        or binding["xml:lang"] != "en"
        or not isinstance(binding["value"], str)
        or not binding["value"].strip()
        or len(binding["value"]) > limit
    ):
        raise FetchError("INSPIRE literal language or schema changed")
    return binding["value"]


def fetch_catalogue(source, client):
    if (source.adapter, source.url, source.config) != ("json", URL, CONFIG):
        raise FetchError("INSPIRE catalogue outside reviewed profile")
    request_url = (
        URL + "?" + urlencode({"query": QUERY, "format": "application/sparql-results+json"})
    )
    response = client.get(request_url, headers={"Accept": "application/sparql-results+json"})
    if response.status != 200 or response.url != request_url:
        raise FetchError("INSPIRE catalogue response identity changed")
    try:
        data = json.loads(response.body)
        rows = data["results"]["bindings"]
        valid = data["head"]["vars"] == ["scheme", "label", "description"] and isinstance(
            rows, list
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError("INSPIRE catalogue invalid schema") from exc
    if not valid or not rows or len(rows) > CONFIG["limit"]:
        raise FetchError("INSPIRE catalogue empty, schema changed or budget exceeded")
    entries = []
    previous = ""
    for row in rows:
        if (
            not isinstance(row, dict)
            or not {"scheme", "label"} <= set(row)
            or set(row) - {"scheme", "label", "description"}
        ):
            raise FetchError("INSPIRE catalogue row schema changed")
        binding = row["scheme"]
        if (
            not isinstance(binding, dict)
            or set(binding) != {"type", "value"}
            or binding["type"] != "uri"
            or not isinstance(binding["value"], str)
            or not URI.fullmatch(binding["value"])
            or binding["value"] <= previous
        ):
            raise FetchError("INSPIRE catalogue identity or ordering changed")
        uri = binding["value"]
        label = literal(row["label"], 1000)
        description = literal(row["description"], 30000) if "description" in row else ""
        entry = safe_entry(
            Entry(
                uri,
                label,
                uri,
                description,
                metadata={
                    "record_kind": "dataset_catalogue",
                    "rdf_uri": uri,
                    "evidence_url": EVIDENCE,
                    "catalogue_scope": NOTE,
                    "description_raw": description,
                    "code_values_downloaded": False,
                },
            ),
            URL,
        )
        if not entry:
            raise FetchError("INSPIRE catalogue invalid entry")
        entries.append(entry)
        previous = uri
    digest = hashlib.sha256("".join(e.digest() for e in entries).encode()).hexdigest()
    return entries, 0, response, digest
