"""GIOŚ yearly result tables from exact reviewed dane.gov.pl files (registries 47 and 48).

The new dane.gios.gov.pl API stays blocked by its HTML robots.txt. These are the
separately published CC BY 4.0 annual files: groundwater physico-chemical results
2019-2024 (dataset 1405) and road-noise measurements 2018-2024 (dataset 1404).
Each file is pinned by resource id, link, size and SHA-256; one channel per year.
Units come only from the bracketed source header; a header without one stays
UNCONFIRMED. The 2023/2024 noise files are legacy XLS, read via ingestion.xls_table.
"""

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta

from django.conf import settings

from ingestion.gios_major_accidents import API, MEDIA, check_dataset, get_json, voivodeship
from ingestion.transport import FetchError
from ingestion.xls_table import read_single_sheet as read_xls_sheet
from ingestion.xlsx_table import read_single_sheet

CONFIG_MODE = "gios_tables"
GW = "groundwater"
NOISE = "noise_road"
DATASETS = {GW: 1405, NOISE: 1404}
REGISTRY = {GW: 47, NOISE: 48}
ATTRIBUTION = {
    GW: (
        "Główny Inspektor Ochrony Środowiska, Państwowy Monitoring Środowiska — monitoring "
        "jakości wód podziemnych; dane.gov.pl zbiór 1405, CC BY 4.0 według metadanych "
        "katalogu. Dane przetworzone: tabela szeroka zachowana, liczby odczytane z tekstu "
        "źródłowego, jednostki wyłącznie z nagłówków. Nie jest to nowe API dane.gios.gov.pl."
    ),
    NOISE: (
        "Główny Inspektor Ochrony Środowiska, Państwowy Monitoring Środowiska — monitoring "
        "hałasu drogowego; dane.gov.pl zbiór 1404, CC BY 4.0 według metadanych katalogu. "
        "Dane przetworzone: LAeq [dB] i okresy pomiaru z tabeli rocznej; strefa czasowa "
        "dat źródłowych niepotwierdzona. Nie jest to nowe API dane.gios.gov.pl."
    ),
}
GW_TITLE = (
    "Wyniki badań w {}r. wskaźników fizykochemicznych {} - monitoring jakości wód "
    "podziemnych - monitoring {}"
)


def _gw(rid, link, size, sha, year, part, programme, fmt, formula_columns=()):
    return {
        "resource": rid,
        "link": MEDIA + link,
        "size": size,
        "sha256": sha,
        "title": GW_TITLE.format(year, part, programme),
        "format": fmt,
        "part": part.casefold(),
        "formula_columns": set(formula_columns),
    }


GW_STEM = "_wska%C5%BAnik%C3%B3w_fizykochemicznych_{}_-_monitoring_jako%C5%9Bci_w%C3%B3d"
GW_STEM2 = "_wska%C5%BAnik%C3%B3w_{}monitoringu_jako%C5%9Bci_w%C3%B3d"
RESOURCES = {
    (GW, 2019): [
        _gw(
            27513,
            "20210115/2019_-_Wyniki_bada%C5%84"
            + GW_STEM.format("NIEORGANICZNYCH")
            + "_podziemnych_-_monitoring_diagnostyczny.csv",
            852815,
            "1858761429963e07bdb46ba1e277bcf98f95823eff674d0098558a633915558d",
            2019,
            "NIEORGANICZNYCH",
            "diagnostyczny",
            "csv",
        ),
        _gw(
            27515,
            "20210115/2019_-_Wyniki_bada%C5%84"
            + GW_STEM.format("ORGANICZNYCH")
            + "_podziemnych_-_monitoring_diagnostyczny.csv",
            145744,
            "aa62e41dd68920779e2a918289f92c8816e7faa851adaa7bc39adbdfea52df94",
            2019,
            "ORGANICZNYCH",
            "diagnostyczny",
            "csv",
        ),
    ],
    (GW, 2020): [
        _gw(
            27516,
            "20210115/2020_-_Wyniki_bada%C5%84"
            + GW_STEM.format("NIEORGANICZNYCH")
            + "_podziemnych_-_monitoring_operacyjny.csv",
            509196,
            "d3b13f205bb07116422c3db74529cc8455b149133197934a135eb8727674b1d2",
            2020,
            "NIEORGANICZNYCH",
            "operacyjny",
            "csv",
        ),
        _gw(
            27517,
            "20210115/2020_-_Wyniki_bada%C5%84"
            + GW_STEM.format("ORGANICZNYCH")
            + "_podziemnych_-_monitoring_operacyjny.csv",
            95600,
            "6f1c07c323277472a38b51c14619ef51738303960aeaadf2f59feb2a5f9dc86e",
            2020,
            "ORGANICZNYCH",
            "operacyjny",
            "csv",
        ),
    ],
    (GW, 2021): [
        _gw(
            43249,
            "20221209/2021_-_Wyniki_bada%C5%84"
            + GW_STEM.format("NIEORGANICZNYCH")
            + "_podziemnych_-_monitoring_operacyjny.xlsx",
            362574,
            "131ef980e713b2a270f932a7490468aa95b9ff6f99cf8a75b462a12c202d1758",
            2021,
            "NIEORGANICZNYCH",
            "operacyjny",
            "xlsx",
        ),
        _gw(
            43294,
            "20221214/2021_-_Wyniki_bada%C5%84"
            + GW_STEM.format("ORGANICZNYCH")
            + "_podziemnych_-_monitoring_operacyjny_RqkQPXW.xlsx",
            149087,
            "421b9516449e0f8229715a3dfc94b878a7974b060325f1d950721f2fbcece9de",
            2021,
            "ORGANICZNYCH",
            "operacyjny",
            "xlsx",
        ),
    ],
    (GW, 2022): [
        _gw(
            53248,
            "20231215/2022_-_Wyniki_bada%C5%84"
            + GW_STEM.format("NIEORGANICZNYCH")
            + "_podziemnych_-_monitoring_diagnostyczny.xlsx",
            767466,
            "994af8a8218394786831560c562a765dfca2341772f02980c5a408b0ab46efbc",
            2022,
            "NIEORGANICZNYCH",
            "diagnostyczny",
            "xlsx",
        ),
        _gw(
            53247,
            "20231215/2022_-_Wyniki_bada%C5%84"
            + GW_STEM.format("ORGANICZNYCH")
            + "_podziemnych_-_monitoring_diagnostyczny.xlsx",
            166051,
            "8edbbba984034688a12824fa7e79983c222d1595142c9df43fee8057430eeeaf",
            2022,
            "ORGANICZNYCH",
            "diagnostyczny",
            "xlsx",
        ),
    ],
    (GW, 2023): [
        _gw(
            63158,
            "20241223/2023_-_Wyniki_bada%C5%84"
            + GW_STEM2.format("fizykochemicznych_")
            + "_podziemnych_-_monitoring_operacyjny.xlsx",
            409060,
            "4a0fe47047a43a9edcff27b77a393df8372ee93274274717c7b198abc2841fa9",
            2023,
            "NIEORGANICZNYCH",
            "operacyjny",
            "xlsx",
        ),
        _gw(
            63161,
            "20241223/2023_-_Wyniki_bada%C5%84"
            + GW_STEM2.format("ORGANICZNYCH_")
            + "_podziemnych_-_monitoring_operacyjny_WYUJM4V.xlsx",
            238827,
            "42851a8b9b031d0576f3fb69e1250c1e297002c381f1cc1bd7118475c93895e7",
            2023,
            "ORGANICZNYCH",
            "operacyjny",
            "xlsx",
        ),
    ],
    (GW, 2024): [
        _gw(
            63162,
            "20241223/2024_-_Wyniki_bada%C5%84"
            + GW_STEM2.format("fizykochemicznych_")
            + "_podziemnych_-_monitoring_operacyjny_A9aCLHe.xlsx",
            347545,
            "4544db069ef71175064aa1be90748a8f37bd02dd2c8984e2f17accdce6553f9e",
            2024,
            "NIEORGANICZNYCH",
            "operacyjny",
            "xlsx",
        ),
        _gw(
            63159,
            "20241223/2024_-_Wyniki_bada%C5%84"
            + GW_STEM2.format("ORGANICZNYCH_")
            + "_podziemnych_-_monitoring_operacyjny.xlsx",
            248099,
            "b71a28b27fb30b29130d9674125ecb5cb25bfe7c59df413db9a516f78f076945",
            2024,
            "ORGANICZNYCH",
            "operacyjny",
            "xlsx",
            formula_columns=("LF",),
        ),
    ],
}
NOISE_STEM = "Wyniki_pomiar%C3%B3w_ha%C5%82asu_drogowego_w_{}_roku"
NOISE_FILES = {
    2018: (
        30180,
        "20210518",
        "",
        838893,
        "d3dc3e02837fe28853fa04e88626488a84e0ef39481a7d57ea1414eb9894db39",
    ),
    2019: (
        30181,
        "20210518",
        "",
        667296,
        "b5a0a66cec54e467d11692a088cb1918dc7b7e0e3015d62eea0fcfb6f2cb89f6",
    ),
    2020: (
        33678,
        "20211008",
        "",
        681219,
        "7774c5547cb3c9ba7362d6ff36e0c74cb698703d1dc250bdc301542012d45ce9",
    ),
    2021: (
        43245,
        "20221209",
        "_doqZu4X",
        879010,
        "7592aa538533def48d37c93ceb6dc609216e5db23eaea539279fecbdee72d3d0",
    ),
    2022: (
        53242,
        "20231215",
        "_TKgZjUy",
        991744,
        "cf5d03bdd269f590d244fedd00fc89159c05a4b83eba1f255fb9eaaa8f47ea1e",
    ),
}
NOISE_DATE_FORMATS = {
    2018: ("%Y-%m-%d %H:%M:%S", "minute"),
    2019: ("%Y-%m-%d %H:%M:%S", "minute"),
    2020: ("%d.%m.%Y %H:%M", "minute"),
    2021: ("%d.%m.%Y", "day"),
    2022: ("%Y-%m-%d %H:%M:%S", "minute"),
    2023: ("excel_day", "day"),
    2024: ("excel_day", "day"),
}
NOISE_COLUMNS = {
    "Województwo": "voivodeship",
    "Miejscowość": "locality",
    "Kod punktu pomiarowego": "point_code",
    "Nazwa punktu pomiarowego": "point_name",
    "Współrz. pp WGS84 (dł. geogr.)": "longitude",
    "Współrz. pp WGS84 (szer. geogr.)": "latitude",
    "Cel pomiaru": "purpose",
    "Data rozpoczęcia": "start",
    "Data zakończenia": "end",
    "Nazwa odcinka drogi": "road_section",
    "Czas odniesienia": "reference_period",
    "Laeq po korekcie [dB]": "laeq_db",
}
for _year, (_rid, _day, _suffix, _size, _sha) in NOISE_FILES.items():
    RESOURCES[(NOISE, _year)] = [
        {
            "resource": _rid,
            "link": f"{MEDIA}{_day}/{NOISE_STEM.format(_year)}{_suffix}.csv",
            "size": _size,
            "sha256": _sha,
            "title": f"Wyniki pomiarów hałasu drogowego w {_year} roku",
            "format": "csv",
            "part": "road_noise",
            "formula_columns": set(),
        }
    ]
# Reviewed 2026-10-07: this row has one extra field (" pora dnia") that shifts every
# later column; it is reported as rejected instead of being re-aligned by guess.
RESOURCES[(NOISE, 2021)][0]["rejected_rows"] = {
    3015: "Wiersz przesunięty o jedną kolumnę w pliku źródłowym; nie wyrównano."
}
# 2023 is a legacy BIFF8 .xls file, read with ingestion.xls_table; dates are day serials.
RESOURCES[(NOISE, 2023)] = [
    {
        "resource": 63169,
        "link": f"{MEDIA}20241223/{NOISE_STEM.format(2023)}.xls",
        "size": 645120,
        "sha256": "0d46fe405b6f71a600fee0581413ced682e0b9095b65ffcc0526abe3e82b8690",
        "title": "Wyniki pomiarów hałasu drogowego w 2023 roku",
        "format": "xls",
        "part": "road_noise",
        "formula_columns": set(),
    }
]
# Same reviewed resource as the existing on-demand file profile; no archive crawling.
RESOURCES[(NOISE, 2024)] = [
    {
        "resource": 989426,
        "link": f"{MEDIA}20251229/Wyniki_pomiarow_halasu_drogowego_w_2024_roku.xls",
        "size": 754688,
        "sha256": "1104c27c78ac16a821ece3c4958421098d7ba55ee209ce87a88f75707d279a7f",
        "title": "Wyniki pomiarów hałasu drogowego w 2024 roku",
        "format": "xls",
        "part": "road_noise",
        "formula_columns": set(),
    }
]
NOISE_PERIODS = {"Dzień 16h", "Dzień 12h", "Wieczór 4h", "Noc 8h"}
NUMBER = re.compile(r"(?P<q>[<>])?\s*(?P<n>-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)")
UNIT = re.compile(r"(?P<name>.+?)\s*\[(?P<unit>[^\[\]]+)\]")


def profile(product, year):
    if (product, year) not in RESOURCES:
        raise FetchError("Unknown reviewed GIOS table profile")
    return {
        "slug": f"gios-{product.replace('_', '-')}-{year}",
        "url": f"https://dane.gov.pl/pl/dataset/{DATASETS[product]}",
        "config": {"mode": CONFIG_MODE, "product": product, "year": year},
        "registry_id": REGISTRY[product],
        "attribution": ATTRIBUTION[product],
    }


def check_resource(record, product, spec):
    attributes = record["attributes"]
    dataset = {"type": "dataset", "id": str(DATASETS[product])}
    if (
        record["relationships"]["dataset"]["data"] != dataset
        or attributes.get("link") != spec["link"]
        or attributes.get("file_size") != spec["size"]
        or attributes.get("format") != spec["format"]
        or attributes.get("title") != spec["title"]
        or attributes.get("contains_protected_data") is not False
    ):
        raise FetchError("GIOS resource version, link or protection flag changed")


def number(value):
    match = NUMBER.fullmatch(value.strip())
    if not match:
        raise FetchError("GIOS table numeric value invalid")
    return match["q"], float(match["n"].replace(",", "."))


def csv_rows(body):
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = body.decode("cp1250")
        except UnicodeDecodeError as exc:
            raise FetchError("GIOS CSV encoding not recognised") from exc
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


def read_rows(body, spec):
    if spec["format"] == "csv":
        return csv_rows(body)
    formulas = []
    reader = read_xls_sheet if spec["format"] == "xls" else read_single_sheet
    rows = reader(body, max_rows=5000, max_columns=400, formula_cells=formulas)
    columns = {re.match(r"[A-Z]+", ref)[0] for ref in formulas}
    if not columns <= spec["formula_columns"]:
        raise FetchError("GIOS table has formulas outside the reviewed columns")
    return rows


def trim(rows, width, skipped=()):
    """Data rows with trailing blanks removed; ``skipped`` are reviewed 1-based row numbers."""
    data = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    if rows[1 : len(data) + 1] != data:
        raise FetchError("GIOS table has blank rows inside the data")
    kept = [(n, row) for n, row in enumerate(data, 1) if n not in skipped]
    if (
        not kept
        or any(n > len(data) for n in skipped)
        # A reviewed skip must still be the same overflowing row, not a valid one.
        or any(not any(cell.strip() for cell in data[n - 1][width:]) for n in skipped)
        or any(any(cell.strip() for cell in row[width:]) for _, row in kept)
    ):
        raise FetchError("GIOS table empty or has unreviewed columns")
    return kept


def groundwater(spec, year, rows):
    header = rows[0]
    if "Data poboru próbki" not in header or header[:1] != ["L.p."] or "Rok badań" not in header:
        raise FetchError("GIOS groundwater header changed")
    split = header.index("Data poboru próbki") + 1
    if split >= len(header) or len(set(header)) != len(header):
        raise FetchError("GIOS groundwater parameter columns missing or duplicated")
    parameters = []
    for column, name in enumerate(header[split:], split):
        match = UNIT.fullmatch(name.strip())
        parameters.append(
            {
                "column": column,
                "source_header": name,
                "name": match["name"] if match else name.strip(),
                "unit": match["unit"] if match else None,
                "unit_status": "source_header" if match else "UNCONFIRMED",
            }
        )
    year_column = header.index("Rok badań")
    data = trim(rows, len(header))
    # L.p. is the source identifier; 2021/2022 files list it out of row order.
    numbers = [row[0].strip().rstrip(".") for _, row in data]
    if sorted(numbers, key=lambda n: int(n) if n.isdigit() else 0) != [
        str(n) for n in range(1, len(data) + 1)
    ]:
        raise FetchError("GIOS groundwater numbering changed")
    records = []
    for (_, row), number_text in zip(data, numbers, strict=True):
        index = int(number_text)
        row = row + [""] * (len(header) - len(row))
        if row[year_column].strip() != str(year):
            raise FetchError("GIOS groundwater year changed")
        raw = row[split - 1].strip()
        if spec["format"] == "csv":
            try:
                sampled = date.fromisoformat(raw)
            except ValueError as exc:
                raise FetchError("GIOS groundwater sampling date invalid") from exc
        elif re.fullmatch(r"[1-9][0-9]{4}", raw):
            sampled = date(1899, 12, 30) + timedelta(days=int(raw))
        else:
            raise FetchError("GIOS groundwater sampling date invalid")
        if sampled.year != year:
            raise FetchError("GIOS groundwater sampling date outside reviewed year")
        results = []
        for value in row[split:]:
            value = value.strip()
            if not value:
                results.append(None)
            elif value == "n.o.":
                # Source code kept verbatim; no numeric meaning is inferred.
                results.append({"code": "n.o."})
            else:
                qualifier, parsed = number(value)
                results.append({"qualifier": qualifier, "value": parsed} if qualifier else parsed)
        records.append(
            {
                "id": f"{spec['resource']}:{index}",
                "source_row_number": index,
                "date": sampled.isoformat(),
                "site": [cell.strip() for cell in row[1 : split - 1]],
                "results": results,
            }
        )
    records.sort(key=lambda record: record["source_row_number"])
    return {
        "site_columns": header[1 : split - 1],
        "parameters": parameters,
        "coordinate_columns_crs": "PUWG 1992 (EPSG:2180) według nazwy kolumny; wartości surowe",
        "records": records,
    }


def noise_date(value, pattern):
    if pattern == "excel_day":
        # Whole-day serials only; a fractional time part is not accepted silently.
        if not re.fullmatch(r"[1-9][0-9]{4}", value):
            raise FetchError("GIOS noise measurement period invalid")
        return datetime(1899, 12, 30) + timedelta(days=int(value))
    try:
        return datetime.strptime(value, pattern)
    except ValueError as exc:
        raise FetchError("GIOS noise measurement period invalid") from exc


def noise(spec, year, rows):
    header = [cell.replace("\xa0", " ").strip() for cell in rows[0]]
    while header and not header[-1]:
        header.pop()
    if sorted(header) != sorted(NOISE_COLUMNS):
        raise FetchError("GIOS noise header changed")
    fields = [NOISE_COLUMNS[name] for name in header]
    pattern, precision = NOISE_DATE_FORMATS[year]
    skipped = spec.get("rejected_rows", {})
    records = []
    for index, row in trim(rows, len(header), skipped):
        cells = dict(zip(fields, [cell.strip() for cell in row], strict=False))
        start, end = noise_date(cells["start"], pattern), noise_date(cells["end"], pattern)
        _, longitude = number(cells["longitude"])
        _, latitude = number(cells["latitude"])
        # A blank level stays null (one 2020 row); it is never filled or inferred.
        qualifier, laeq = number(cells["laeq_db"]) if cells["laeq_db"] else (None, None)
        if (
            qualifier
            or end < start
            or cells["reference_period"] not in NOISE_PERIODS
            or start.year != year
            or not (14 <= longitude <= 24.5 and 49 <= latitude <= 55)
            or (laeq is not None and not 0 < laeq < 150)
            or not re.fullmatch(r"D_[0-9]{2}_[0-9]{6}_[0-9]{3}", cells["point_code"])
        ):
            raise FetchError("GIOS noise period, coordinates, level or point code invalid")
        fmt = "%Y-%m-%d" if precision == "day" else "%Y-%m-%dT%H:%M"
        records.append(
            {
                "id": f"{spec['resource']}:{index}",
                "source_row_number": index,
                "voivodeship": voivodeship(cells["voivodeship"]),
                "locality": cells["locality"],
                "point_code": cells["point_code"],
                "point_name": cells["point_name"],
                "longitude": longitude,
                "latitude": latitude,
                "purpose": cells["purpose"],
                "road_section": cells["road_section"],
                "reference_period": cells["reference_period"],
                "start": start.strftime(fmt),
                "end": end.strftime(fmt),
                "laeq_db": laeq,
            }
        )
    return {
        "parameters": [
            {
                "name": "LAeq po korekcie",
                "field": "laeq_db",
                "unit": "dB",
                "unit_status": "source_header",
            }
        ],
        "date_precision": precision,
        "source_timezone": "UNCONFIRMED",
        "rejected_rows": [
            {"source_row_number": number, "reason": reason}
            for number, reason in sorted(skipped.items())
        ],
        "records": records,
    }


def parse_file(product, year, spec, body):
    if len(body) != spec["size"] or hashlib.sha256(body).hexdigest() != spec["sha256"]:
        raise FetchError("GIOS table file differs from the reviewed version")
    rows = read_rows(body, spec)
    if not rows:
        raise FetchError("GIOS table empty")
    return (groundwater if product == GW else noise)(spec, year, rows)


def collect(channel, client):
    config = channel.config
    product, year = config.get("product"), config.get("year")
    expected = profile(product, year) if isinstance(year, int) else None
    if not expected or (channel.url, channel.region, channel.kind, config) != (
        expected["url"],
        "PL",
        "table",
        expected["config"],
    ):
        raise FetchError("GIOS table profile differs from reviewed scope")
    dataset = DATASETS[product]
    check_dataset(get_json(client, f"{API}datasets/{dataset}", "dataset", dataset))
    tables, records = [], 0
    for spec in RESOURCES[(product, year)]:
        check_resource(
            get_json(client, f"{API}resources/{spec['resource']}", "resource", spec["resource"]),
            product,
            spec,
        )
        response = client.get(spec["link"])
        if response.url != spec["link"]:
            raise FetchError("GIOS table final URL changed")
        table = parse_file(product, year, spec, response.body)
        records += len(table["records"])
        tables.append(
            {
                "resource_id": spec["resource"],
                "title": spec["title"],
                "part": spec["part"],
                "url": spec["link"],
                "sha256": spec["sha256"],
                "bytes": spec["size"],
                **table,
            }
        )
    data = {
        "schema": f"gios-{product}-v1",
        "product": product,
        "year": year,
        "attribution": ATTRIBUTION[product],
        "license": "CC BY 4.0",
        "dataset_url": expected["url"],
        "tables": tables,
    }
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("GIOS table payload exceeds automatic budget")
    unconfirmed = sorted(
        {p["name"] for t in tables for p in t["parameters"] if p["unit_status"] != "source_header"}
    )
    summary = {
        "kind": "gios_tables",
        "collector": "gios_tables",
        "product": product,
        "year": year,
        "scope": "PL",
        "records": records,
        "rejected": sum(len(t.get("rejected_rows", [])) for t in tables),
        "parameters": sum(len(t["parameters"]) for t in tables),
        "units_unconfirmed": unconfirmed,
        "partial": False,
        "new_api_used": False,
    }
    return payload, "application/json", summary
