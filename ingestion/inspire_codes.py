"""Reviewed INSPIRE SKOS schemes, one bounded GET per profile, no URI child requests.

Profile "base": eight schemes (active since 2026-10-02). Profile "data": ten code
lists tied to data GeoKlimat already collects (user card 2026-10-08T12:07Z), kept in a
separate source so the active channel's configuration and history stay unchanged.
"""

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
DATA_SCHEMES = {
    "http://inspire.ec.europa.eu/codelist/" + key: label
    for key, label in [
        ("BoreholePurposeValue", "Cel wiercenia otworu (CBDG)"),
        ("CommodityCodeValue", "Kopaliny (złoża MIDAS)"),
        ("DesignationSchemeValue", "Schematy wyznaczania form ochrony"),
        ("Natura2000DesignationValue", "Typy obszarów Natura 2000"),
        ("ProtectionClassificationValue", "Klasyfikacja ochrony obszarów"),
        ("MeasurementRegimeValue", "Reżim pomiarów stacji"),
        ("PurposeOfCollectionValue", "Cel zbierania danych"),
        ("AquiferTypeValue", "Typ warstwy wodonośnej"),
        ("NaturalHazardCategoryValue", "Kategorie zagrożeń naturalnych"),
        ("ZoneTypeCode", "Typy stref zarządzania"),
    ]
}
DATA_CONFIG = {"mode": "inspire_codes", "schemes": sorted(DATA_SCHEMES), "limit": 2000}
# Read on the EU SPARQL endpoint 2026-10-08T20:15Z: the scheme exists (labels, definition,
# extensibility) but holds no skos:Concept — an empty, extensible INSPIRE list. Only this
# reviewed scheme may come back empty; any other missing list still fails the batch.
REVIEWED_EMPTY_SCHEMES = {"http://inspire.ec.europa.eu/codelist/PurposeOfCollectionValue"}
DATA_SLUG = "registry-180-inspire-data-codes"
ALL_SCHEMES = {**SCHEMES, **DATA_SCHEMES}
NOTE = (
    "Wartości ośmiu wybranych schematów INSPIRE: tematy, domeny/media/rodzaje "
    "środowiska, stan wód podziemnych, zasolenie, trwałość wody i cel badania gleby. "
    "Nazwy PL od dostawcy, w razie braku EN; definicje EN. Jedna odpowiedź, "
    "do1000wartości. Bez statusów obowiązywania, hierarchii, geometrii, pełnego "
    "rejestru i wymyślonych dat. Nie potwierdza zmian prawa ani aktualności kodu."
)
DATA_NOTE = (
    "Wartości dziesięciu list kodowych INSPIRE powiązanych z danymi GeoKlimatu: cel "
    "wiercenia, kopaliny, formy ochrony i Natura 2000, reżim i cel pomiarów, typ warstwy "
    "wodonośnej, zagrożenia naturalne, strefy zarządzania. Nazwy PL od dostawcy, w razie "
    "braku EN; definicje EN. Jedna odpowiedź, do2000wartości. Bez hierarchii, statusów "
    "obowiązywania i powiązania z rekordami danych."
)
PROFILES = {
    SLUG: (CONFIG, SCHEMES, NOTE),
    DATA_SLUG: (DATA_CONFIG, DATA_SCHEMES, DATA_NOTE),
}


def query(schemes, limit):
    return (
        "PREFIX skos: <http://www.w3.org/2004/02/skos/core#> "
        "SELECT DISTINCT ?scheme ?concept ?label ?label_pl ?definition WHERE { "
        "VALUES ?scheme { " + " ".join("<" + uri + ">" for uri in sorted(schemes)) + " } "
        "?concept a skos:Concept; skos:inScheme ?scheme; skos:prefLabel ?label . "
        'FILTER(LANG(?label)="en") '
        'OPTIONAL { ?concept skos:prefLabel ?label_pl FILTER(LANG(?label_pl)="pl") } '
        'OPTIONAL { ?concept skos:definition ?definition FILTER(LANG(?definition)="en") } '
        f"}} ORDER BY ?scheme ?concept ?label ?label_pl ?definition LIMIT {limit + 1}"
    )


QUERY = query(SCHEMES, CONFIG["limit"])


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


# Live 2026-10-08: the ten-list query in one request timed out (15 s) twice while
# other queries on the same endpoint succeeded; the data profile asks per list.
PER_LIST_LIMIT = 1000


def query_rows(client, schemes, limit):
    request_url = (
        URL
        + "?"
        + urlencode(
            {
                "query": query(schemes, limit),
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
    if not valid or not isinstance(rows, list) or len(rows) > limit:
        raise FetchError("INSPIRE codes empty/truncated/budget invalid")
    return rows, response


def fetch_codes(source, client):
    profile = PROFILES.get(source.slug)
    if not profile or (source.adapter, source.url, source.config) != ("json", URL, profile[0]):
        raise FetchError("INSPIRE codes outside reviewed profile")
    config, schemes, note = profile
    if source.slug == DATA_SLUG:
        rows = []
        for uri in sorted(schemes):
            part, response = query_rows(client, {uri: schemes[uri]}, PER_LIST_LIMIT)
            rows += part
    else:
        rows, response = query_rows(client, schemes, config["limit"])
    if not 1 <= len(rows) <= config["limit"]:
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
            scheme not in schemes
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
                    "catalogue_scope": note,
                    "inspire_code_value": True,
                    "rdf_uri": concept,
                    "scheme_uri": scheme,
                    "scheme_label": schemes[scheme],
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
    missing = set(schemes) - seen_schemes - REVIEWED_EMPTY_SCHEMES
    if missing:
        missing = sorted(uri.rsplit("/", 1)[-1] for uri in missing)
        raise FetchError("INSPIRE codes missing reviewed scheme: " + ", ".join(missing)[:200])
    digest = hashlib.sha256("".join(entry.digest() for entry in entries).encode()).hexdigest()
    return entries, 0, response, digest
