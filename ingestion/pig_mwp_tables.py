"""PIG-PIB groundwater monitoring (MWP) attribute tables: measurements and chemistry.

Registry 63. Tables 1 and 2 of hydrogeologia/mwp/MapServer are read in full on each
run: pinned OBJECTID manifest before and after, offset pages of 1000 ordered by
OBJECTID whose identities must equal the manifest slice, no geometry. The provider
publishes a window (measurements: one month, chemistry: one sampling year), not an
archive; history exists only as the copies kept here. Values are kept as published;
dates only lose the provider's leading space. Repeated strings of the chemistry table
are dictionary-encoded so the full table fits one copy; nothing is dropped.
"""

import json
import math
import re
from urllib.parse import urlencode

from django.conf import settings

from ingestion.pig_data import TERMS, read
from ingestion.transport import FetchError

REGISTRY_ID = 63
BASE = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/hydrogeologia/mwp/MapServer/"
EVIDENCE = "https://baza.pgi.gov.pl/geoportal/uslugi/gis"
PAGE = 1000
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TABLES = {
    "pomiary": {
        "layer": 1,
        "name": "MWP - pomiary",
        "title": "MWP — pomiary zwierciadła i wydajności",
        "max_rows": 100000,
        "fields": {
            "OBJECTID": "esriFieldTypeOID",
            "OTWOR_ID": "esriFieldTypeInteger",
            "DATA_POMIARU": "esriFieldTypeString",
            "GLEBOKOSC_ZWIERCIADLA": "esriFieldTypeDouble",
            "WYDAJNOSC": "esriFieldTypeDouble",
        },
        "date": "DATA_POMIARU",
        "dictionary": [],
        "units": {"GLEBOKOSC_ZWIERCIADLA": "m p.p.t.", "WYDAJNOSC": "l/s"},
        "note": "Okno dostawcy: pomiary z jednego miesiąca; starsze miesiące nie są publikowane.",
    },
    "chemia": {
        "layer": 2,
        "name": "MWP - wskaźniki chemiczne",
        "title": "MWP — wskaźniki chemiczne",
        "max_rows": 200000,
        "fields": {
            "OBJECTID": "esriFieldTypeOID",
            "OTWOR_ID": "esriFieldTypeInteger",
            "BAZA_HISTORYCZNA": "esriFieldTypeString",
            "DATA_POBORU": "esriFieldTypeString",
            "OZNACZENIE": "esriFieldTypeString",
            "WSKAZNIK": "esriFieldTypeString",
            "SYMBOL_CHEMICZNY": "esriFieldTypeString",
            "ZNAK": "esriFieldTypeString",
            "WARTOSC": "esriFieldTypeDouble",
            "JEDNOSTKA": "esriFieldTypeString",
        },
        "date": "DATA_POBORU",
        "dictionary": [
            "BAZA_HISTORYCZNA",
            "OZNACZENIE",
            "WSKAZNIK",
            "SYMBOL_CHEMICZNY",
            "ZNAK",
            "JEDNOSTKA",
        ],
        "units": {},
        "note": (
            "Okno dostawcy: jeden rok poborów. Jednostka w kolumnie JEDNOSTKA; "
            "ZNAK (np. wartość poniżej LOQ) zachowany bez interpretacji."
        ),
    },
}
ATTRIBUTION = (
    "PIG-PIB / PSH — monitoring wód podziemnych (MWP), tabele atrybutowe usługi "
    "hydrogeologia/mwp. Pełny odczyt tabeli przy każdym pobraniu; okno publikacji "
    "dostawcy, nie archiwum. Czas wytworzenia niepotwierdzony. Warunki: " + TERMS
)


def channel_url(key):
    return BASE + str(TABLES[key]["layer"]) + "/query"


def config(key):
    return {"mode": "pig_mwp_table", "table": key, "layer": TABLES[key]["layer"]}


def slug(key):
    return "pig-mwp-" + key


def profile(channel):
    key = channel.config.get("table")
    if (
        key not in TABLES
        or channel.config != config(key)
        or channel.url != channel_url(key)
        or channel.kind != "table"
        or channel.region != "PL"
    ):
        raise FetchError("MWP table outside reviewed profile")
    return key, TABLES[key]


def check_schema(client, url, spec):
    data = read(client, url.removesuffix("/query") + "?f=json")
    fields = data.get("fields")
    if (
        data.get("name") != spec["name"]
        or data.get("type") != "Table"
        or not isinstance(fields, list)
        or {f.get("name"): f.get("type") for f in fields if isinstance(f, dict)} != spec["fields"]
    ):
        raise FetchError("MWP table identity/schema changed")


def manifest(client, url, spec):
    data = read(
        client, url + "?" + urlencode({"f": "json", "where": "1=1", "returnIdsOnly": "true"})
    )
    ids = data.get("objectIds")
    if (
        data.get("objectIdFieldName") != "OBJECTID"
        or not isinstance(ids, list)
        or not ids
        or len(ids) > spec["max_rows"]
        or any(type(i) is not int or not 0 <= i <= 2**53 for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise FetchError("MWP identity manifest missing, repeated or outside budget")
    count = read(
        client, url + "?" + urlencode({"f": "json", "where": "1=1", "returnCountOnly": "true"})
    ).get("count")
    if type(count) is not int or count != len(ids):
        raise FetchError("MWP manifest does not match reported count")
    return sorted(ids)


def page_url(url, spec, offset):
    return (
        url
        + "?"
        + urlencode(
            {
                "f": "json",
                "where": "1=1",
                "outFields": ",".join(spec["fields"]),
                "returnGeometry": "false",
                "orderByFields": "OBJECTID ASC",
                "resultOffset": offset,
                "resultRecordCount": PAGE,
            }
        )
    )


def value(field, kind, raw, date_field):
    if raw is None:
        if kind in {"esriFieldTypeOID", "esriFieldTypeInteger"} or field == date_field:
            raise FetchError("MWP identity or date missing")
        return None
    if kind in {"esriFieldTypeOID", "esriFieldTypeInteger"}:
        if type(raw) is not int:
            raise FetchError("MWP integer field changed type")
        return raw
    if kind == "esriFieldTypeDouble":
        if type(raw) not in (int, float) or not math.isfinite(raw):
            raise FetchError("MWP numeric field changed type")
        return raw
    if not isinstance(raw, str):
        raise FetchError("MWP text field changed type")
    if field == date_field:
        raw = raw.strip()
        if not DATE.fullmatch(raw):
            raise FetchError("MWP date not YYYY-MM-DD")
    return raw


def collect(channel, client):
    key, spec = profile(channel)
    url = channel.url
    check_schema(client, url, spec)
    ids = manifest(client, url, spec)
    columns = list(spec["fields"])
    dictionaries = {name: [] for name in spec["dictionary"]}
    positions = {name: {} for name in spec["dictionary"]}
    rows = []
    for offset in range(0, len(ids), PAGE):
        data = read(client, page_url(url, spec, offset))
        features = data.get("features")
        expected = ids[offset : offset + PAGE]
        if not isinstance(features, list) or len(features) != len(expected):
            raise FetchError("MWP page missing or extra rows")
        for feature, identity in zip(features, expected, strict=True):
            attributes = feature.get("attributes") if isinstance(feature, dict) else None
            if not isinstance(attributes, dict) or set(attributes) != set(columns):
                raise FetchError("MWP row columns changed")
            if attributes["OBJECTID"] != identity:
                raise FetchError("MWP page identity/order mismatch")
            row = []
            for name in columns:
                item = value(name, spec["fields"][name], attributes[name], spec["date"])
                if name in positions and item is not None:
                    if item not in positions[name]:
                        positions[name][item] = len(dictionaries[name])
                        dictionaries[name].append(item)
                    item = positions[name][item]
                row.append(item)
            rows.append(row)
    if manifest(client, url, spec) != ids:
        raise FetchError("MWP identity manifest changed during read; retry")
    date_index = columns.index(spec["date"])
    dates = sorted({row[date_index] for row in rows})
    stations = len({row[columns.index("OTWOR_ID")] for row in rows})
    payload = json.dumps(
        {
            "schema": "pig-mwp-table-v1",
            "table": key,
            "title": spec["title"],
            "source_url": url,
            "evidence_url": EVIDENCE,
            "terms_url": TERMS,
            "attribution": ATTRIBUTION,
            "scope_note": spec["note"],
            "station_link": "OTWOR_ID = ID w warstwie punktów MWP (pig-mwp-scan)",
            "units": spec["units"],
            "source_generated_at": None,
            "period": [dates[0], dates[-1]],
            "columns": columns,
            "dictionary_columns": spec["dictionary"],
            "dictionaries": dictionaries,
            "rows": rows,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("MWP table payload exceeds automatic budget")
    return (
        payload,
        "application/json",
        {
            "kind": "pig_mwp_table",
            "collector": "pig_mwp_table",
            "table": key,
            "scope": "PL",
            "records": len(rows),
            "source_count": len(ids),
            "stations": stations,
            "period_start": dates[0],
            "period_end": dates[-1],
            "partial": False,
            "retention_basis": "local_copy_age",
        },
    )
