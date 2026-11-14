"""EU Vocabularies authority tables (registry 180): one bounded SPARQL GET, no values.

Lists SKOS ConceptSchemes under http://publications.europa.eu/resource/authority/ with
their English label and optional version, so new or re-versioned tables (countries,
languages, licences, data themes, corporate bodies...) are visible. It does not fetch
table values, EuroVoc, legal acts or dates; the version string is the provider's.
"""

import hashlib
import json
import re
from urllib.parse import urlencode

from ingestion.adapters import Entry, safe_entry
from ingestion.inspire_vocabularies import URL, literal
from ingestion.transport import FetchError

CONFIG = {"mode": "eu_authority_tables", "language": "en", "limit": 1000}
SLUG = "registry-180-eu-authority-tables"
EVIDENCE = "https://op.europa.eu/en/web/eu-vocabularies/authority-tables"
NOTE = (
    "Katalog tabel autorytatywnych EU Vocabularies (SKOS ConceptScheme w "
    "publications.europa.eu/resource/authority/): angielska nazwa i wersja podana przez "
    "dostawcę. Do1000tabel w jednej odpowiedzi; bez wartości tabel, EuroVoc, aktów i dat "
    "publikacji. Nowa wersja w katalogu nie opisuje, które wartości się zmieniły."
)
# Live 2026-10-15 (PC): 270 schemes; 18 numbered subsets such as country/0001
# "Current EU members" and one named subset sdmxglossary2018/cross_domain.
URI = re.compile(
    r"http://publications\.europa\.eu/resource/authority/"
    r"[A-Za-z0-9_-]{1,100}(/[A-Za-z0-9_-]{1,60})?\Z"
)
FIELDS = ["scheme", "label", "version"]
QUERY = """PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
SELECT DISTINCT ?scheme ?label ?version WHERE {
 ?scheme a skos:ConceptScheme; skos:prefLabel ?label .
 FILTER(STRSTARTS(STR(?scheme), "http://publications.europa.eu/resource/authority/")
  && LANG(?label)="en")
 OPTIONAL { ?scheme owl:versionInfo ?version }
} ORDER BY ?scheme ?label ?version LIMIT 1001"""


def version_literal(binding):
    if (
        not isinstance(binding, dict)
        or binding.get("type") not in ("literal", "typed-literal")
        # sdmxglossary2018 tags its version "2018"@en (live 2026-10-15).
        or set(binding) - {"type", "value", "datatype", "xml:lang"}
        or not isinstance(binding.get("value"), str)
        or not re.fullmatch(r"[\w.:+-]{1,40}", binding["value"])
    ):
        raise FetchError("EU authority table version changed")
    return binding["value"]


def fetch_catalogue(source, client):
    if (source.adapter, source.url, source.config) != ("json", URL, CONFIG):
        raise FetchError("EU authority tables outside reviewed profile")
    request_url = (
        URL + "?" + urlencode({"query": QUERY, "format": "application/sparql-results+json"})
    )
    response = client.get(request_url, headers={"Accept": "application/sparql-results+json"})
    if response.status != 200 or response.url != request_url:
        raise FetchError("EU authority tables response identity changed")
    try:
        data = json.loads(response.body)
        rows = data["results"]["bindings"]
        valid = data["head"]["vars"] == FIELDS and isinstance(rows, list)
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError("EU authority tables invalid schema") from exc
    if not valid or not rows or len(rows) > CONFIG["limit"]:
        raise FetchError("EU authority tables empty, schema changed or budget exceeded")
    entries = []
    previous = ""
    for row in rows:
        if (
            not isinstance(row, dict)
            or not {"scheme", "label"} <= set(row)
            or set(row) - set(FIELDS)
        ):
            raise FetchError("EU authority tables row schema changed")
        binding = row["scheme"]
        if (
            not isinstance(binding, dict)
            or set(binding) != {"type", "value"}
            or binding["type"] != "uri"
            or not isinstance(binding["value"], str)
            or not URI.fullmatch(binding["value"])
            or binding["value"] <= previous
        ):
            # A second label or version for one table would repeat the URI: refuse the
            # batch rather than pick one silently.
            raise FetchError("EU authority tables identity, ordering or uniqueness changed")
        uri = binding["value"]
        label = literal(row["label"], 1000)
        version = version_literal(row["version"]) if "version" in row else ""
        title = label + (" (wersja " + version + ")" if version else "")
        entry = safe_entry(
            Entry(
                uri,
                title,
                uri,
                "",
                metadata={
                    "record_kind": "dataset_catalogue",
                    "rdf_uri": uri,
                    "evidence_url": EVIDENCE,
                    "catalogue_scope": NOTE,
                    "table_version_raw": version,
                    "table_values_downloaded": False,
                },
            ),
            URL,
        )
        if not entry:
            raise FetchError("EU authority tables invalid entry")
        entries.append(entry)
        previous = uri
    digest = hashlib.sha256("".join(e.digest() for e in entries).encode()).hexdigest()
    return entries, 0, response, digest
