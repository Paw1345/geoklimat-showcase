"""Eight reviewed INSPIRE SKOS schemes, one bounded GET, no URI child requests."""

import hashlib
import json
from urllib.parse import urlencode

from ingestion.adapters import Entry, safe_entry
from ingestion.inspire_vocabularies import EVIDENCE, URI, URL
from ingestion.transport import FetchError

SCHEMES = {
    "http://inspire.ec.europa.eu/theme": "Tematy INSPIRE",
    **{
        "http://inspire.ec.europa.eu/codelist/" + key: label
        for key, label in [
            ("EnvironmentalDomain", "Domeny środowiska"),
            ("MediaTypeValue", "Media środowiskowe"),
            ("EnvironmentValue", "Rodzaje środowiska"),
            ("ConditionOfGroundwaterValue", "Stan wód podziemnych"),
            ("WaterSalinityValue", "Zasolenie wody"),
            ("WaterPersistenceValue", "Trwałość występowania wody"),
            ("SoilInvestigationPurposeValue", "Cel badania gleby"),
        ]
    },
}
CONFIG = {"mode": "inspire_codes", "schemes": sorted(SCHEMES), "limit": 1000}
SLUG = "registry-180-inspire-codes"
NOTE = (
    "Wartości ośmiu wybranych schematów INSPIRE: tematy, domeny/media/rodzaje "
    "środowiska, stan wód podziemnych, zasolenie, trwałość wody i cel badania gleby. "
    "Nazwy PL od dostawcy, w razie braku EN; definicje EN. Jedna odpowiedź, "
    "do1000wartości. Bez statusów obowiązywania, hierarchii, geometrii, pełnego "
    "rejestru i wymyślonych dat. Nie potwierdza zmian prawa ani aktualności kodu."
)
QUERY = (
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#> "
    "SELECT DISTINCT ?scheme ?concept ?label ?label_pl ?definition WHERE { "
    "VALUES ?scheme { " + " ".join("<" + uri + ">" for uri in sorted(SCHEMES)) + " } "
    "?concept a skos:Concept; skos:inScheme ?scheme; skos:prefLabel ?label . "
    'FILTER(LANG(?label)="en") '
    'OPTIONAL { ?concept skos:prefLabel ?label_pl FILTER(LANG(?label_pl)="pl") } '
    'OPTIONAL { ?concept skos:definition ?definition FILTER(LANG(?definition)="en") } '
    "} ORDER BY ?scheme ?concept ?label ?label_pl ?definition LIMIT 1001"
)


def literal(binding, language, limit):
    if (
        not isinstance(binding, dict)
        or set(binding) != {"type", "xml:lang", "value"}
        or binding["type"] != "literal"
        or binding["xml:lang"] != language
        or not isinstance(binding["value"], str)
        or not binding["value"].strip()
        or len(binding["value"]) > limit
    ):
        raise FetchError("INSPIRE code literal schema/language invalid")
    return binding["value"]


def identity(binding):
    if (
        not isinstance(binding, dict)
        or set(binding) != {"type", "value"}
        or binding["type"] != "uri"
        or not isinstance(binding["value"], str)
        or not URI.fullmatch(binding["value"])
    ):
        raise FetchError("INSPIRE code URI invalid")
    return binding["value"]


def unique_pairs(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise FetchError("INSPIRE duplicate JSON key")
        row[key] = value
    return row


def fetch_codes(source, client):
    if (source.adapter, source.url, source.config) != ("json", URL, CONFIG):
        raise FetchError("INSPIRE codes outside reviewed profile")
    request_url = (
        URL
        + "?"
        + urlencode(
            {
                "query": QUERY,
                "format": "application/sparql-results+json",
            }
        )
    )
    response = client.get(request_url, headers={"Accept": "application/sparql-results+json"})
    if response.status != 200 or response.url != request_url:
        raise FetchError("INSPIRE codes response identity changed")
    try:
        data = json.loads(response.body, object_pairs_hook=unique_pairs)
        rows = data["results"]["bindings"]
        valid = data["head"]["vars"] == ["scheme", "concept", "label", "label_pl", "definition"]
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        raise FetchError("INSPIRE codes invalid JSON schema") from exc
    if not valid or not isinstance(rows, list) or not 1 <= len(rows) <= CONFIG["limit"]:
        raise FetchError("INSPIRE codes empty/truncated/budget invalid")
    entries, previous, seen_schemes = [], ("", ""), set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or not {"scheme", "concept", "label"} <= set(row)
            or set(row) - {"scheme", "concept", "label", "label_pl", "definition"}
        ):
            raise FetchError("INSPIRE code row schema invalid")
        scheme, concept = identity(row["scheme"]), identity(row["concept"])
        if (
            scheme not in SCHEMES
            or not concept.startswith(scheme + "/")
            or (scheme, concept) <= previous
        ):
            raise FetchError("INSPIRE codes membership/duplicates/order invalid")
        english = literal(row["label"], "en", 1000)
        polish = literal(row["label_pl"], "pl", 1000) if "label_pl" in row else ""
        definition = literal(row["definition"], "en", 30000) if "definition" in row else ""
        entry = safe_entry(
            Entry(
                scheme + "|" + concept,
                polish or english,
                concept,
                definition,
                metadata={
                    "record_kind": "dataset_catalogue",
                    "catalogue_scope": NOTE,
                    "inspire_code_value": True,
                    "rdf_uri": concept,
                    "scheme_uri": scheme,
                    "scheme_label": SCHEMES[scheme],
                    "label_en": english,
                    "label_pl": polish,
                    "display_language": "pl" if polish else "en",
                    "definition_raw": definition,
                    "definition_language": "en",
                    "evidence_url": EVIDENCE,
                    "code_status_verified": False,
                    "current_legal_status_verified": False,
                },
            ),
            URL,
        )
        if not entry:
            raise FetchError("INSPIRE code entry invalid")
        entries.append(entry)
        previous = (scheme, concept)
        seen_schemes.add(scheme)
    if seen_schemes != set(SCHEMES):
        raise FetchError("INSPIRE codes missing reviewed scheme")
    digest = hashlib.sha256("".join(entry.digest() for entry in entries).encode()).hexdigest()
    return entries, 0, response, digest
