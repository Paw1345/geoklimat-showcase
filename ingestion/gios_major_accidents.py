"""GIOŚ major-accident registers from exact reviewed dane.gov.pl files (registry 50).

The new dane.gios.gov.pl API stays blocked by its HTML robots.txt. These are the
separately published annual CC BY 4.0 tables: ZDR/ZZR establishments as of
2024-12-31 and accidents reported to GIOŚ in 2022-2024. Pinned by resource id,
link, size and header; any change fails closed and requires a new review.
"""

import hashlib
import json
import re
from datetime import date, timedelta

from django.conf import settings

from ingestion.transport import FetchError
from ingestion.xlsx_table import read_single_sheet

SLUG = "gios-major-accidents-tables"
CONFIG = {"mode": "gios_major_accidents", "schema": "gios-major-accidents-v1"}
URL = "https://dane.gov.pl/pl/dataset/137"
API = "https://api.dane.gov.pl/1.4/"
MEDIA = "https://api.dane.gov.pl/media/resources/"
ATTRIBUTION = (
    "Główny Inspektor Ochrony Środowiska; dane.gov.pl zbiory 137 i 2540, CC BY 4.0 "
    "według metadanych katalogu (bez dodatkowych warunków). Dane przetworzone: "
    "odczyt tabel bez zmiany treści komórek. Okres: zgłoszone poważne awarie 2022–2024, "
    "rejestry ZDR/ZZR wg stanu na 31.12.2024. Nie jest to nowe API dane.gios.gov.pl."
)
VOIVODESHIPS = set(
    (
        "dolnośląskie kujawsko-pomorskie lubelskie lubuskie łódzkie małopolskie "
        "mazowieckie opolskie podkarpackie podlaskie pomorskie śląskie "
        "świętokrzyskie warmińsko-mazurskie wielkopolskie zachodniopomorskie"
    ).split()
)
REGISTER_FIELDS = ["source_row_number", "voivodeship", "establishment", "address", "safety_url"]
ACCIDENT_FIELDS = [
    "source_row_number",
    "voivodeship",
    "date",
    "location",
    "source_place",
    "accident_type",
    "description",
    "criteria",
    "effects",
    "pollutants",
    "victims_total",
    "victims_fatal",
]
REGISTER_TAIL = [
    "WOJEWÓDZTWO",
    "NAZWA ZAKŁADU ",
    "ADRES",
    "Adres strony internetowej, \nna której dostępne są informacje na temat środków bezpieczeństwa",
]
ACCIDENTS = [
    "Lp.",
    "Województwo",
    "Data",
    "Miejscowość,\ngmina,\npowiat",
    "Źródło/miejsce awarii",
    "Rodzaj\nawarii",
    "Opis awarii",
    " kryteria RMŚ",
    "Skutki poważnej awarii",
    "Rodzaj (ilość)\nzanieczyszczeń",
    "Ofiary awarii ogółem",
    "Ofiary awarii śmiertelne",
]
ACCIDENTS_2022 = [
    "Lp.",
    "Województwo",
    "Data",
    "Miejscowość, gmina, powiat",
    "Ulica",
    "Źródło/miejsce awarii",
    "Rodzaj \nawarii",
    "Opis awarii",
    "Kryteria RMŚ",
    "Skutki poważnej awarii",
    "Rodzaj (ilość) zanieczyszczeń",
    "Ofiary awarii ogółem",
    "Ofiary awarii śmiertelne",
]
ACCIDENT_FIELDS_2022 = [*ACCIDENT_FIELDS[:4], "street", *ACCIDENT_FIELDS[4:]]
ACCIDENT_TITLE = (
    "{} rok - poważne awarie objęte obowiązkiem zgłoszenia do Głównego Inspektora "
    "Ochrony Środowiska"
)
REGISTER_TITLE = (
    "Zakłady o {} ryzyku wystąpienia poważnej awarii przemysłowej ({}) "
    "wg stanu na dzień 31 grudnia 2024r."
)
RESOURCES = {
    "zdr_2024": {
        "dataset": 2540,
        "resource": 989557,
        "link": MEDIA + "20251229/ZDR_stan_31122024r_vk8KX4K.xlsx",
        "size": 38092,
        "title": REGISTER_TITLE.format("dużym", "ZDR"),
        "header": ["LICZBA ZDR ", *REGISTER_TAIL],
        "fields": REGISTER_FIELDS,
        "table": "register",
        "category": "ZDR",
        "as_of": "2024-12-31",
    },
    "zzr_2024": {
        "dataset": 2540,
        "resource": 989556,
        "link": MEDIA + "20251229/ZZR_stan_31122024r_V8NcOrb.xlsx",
        "size": 44083,
        "title": REGISTER_TITLE.format("zwiększonym", "ZZR"),
        "header": ["LICZBA ZZR ", *REGISTER_TAIL],
        "fields": REGISTER_FIELDS,
        "table": "register",
        "category": "ZZR",
        "as_of": "2024-12-31",
    },
    "accidents_2024": {
        "dataset": 137,
        "resource": 989495,
        "link": MEDIA + "20251229/2024_powaze_awarie_objete_obowiazkiem_zgloszenia_do_GIOS.xlsx",
        "size": 13117,
        "title": ACCIDENT_TITLE.format(2024),
        "header": ACCIDENTS,
        "fields": ACCIDENT_FIELDS,
        "table": "accidents",
        "year": 2024,
    },
    "accidents_2023": {
        "dataset": 137,
        "resource": 63377,
        "link": MEDIA
        + "20250103/2023_rok_-_powa%C5%BCne_awarie_obj%C4%99te_obowi%C4%85zkiem_zg%C5%82oszenia"
        "_do_G%C5%82%C3%B3wnego_Inspektora_Ochrony_%C5%9Arodowiska_eMkKUdy.xlsx",
        "size": 11721,
        "title": ACCIDENT_TITLE.format(2023),
        "header": ACCIDENTS,
        "fields": ACCIDENT_FIELDS,
        "table": "accidents",
        "year": 2023,
    },
    "accidents_2022": {
        "dataset": 137,
        "resource": 53257,
        "link": MEDIA
        + "20231215/2022_rok_-_powa%C5%BCne_awarie_obj%C4%99te_obowi%C4%85zkiem_zg%C5%82oszenia"
        "_do_G%C5%82%C3%B3wnego_Inspektora_Ochrony_%C5%9Arodowiska.xlsx",
        "size": 12510,
        "title": ACCIDENT_TITLE.format(2022),
        "header": ACCIDENTS_2022,
        "fields": ACCIDENT_FIELDS_2022,
        "table": "accidents",
        "year": 2022,
    },
}
DATASETS = {137, 2540}
UNITS = {"victims_total": "osoby", "victims_fatal": "osoby"}


def get_json(client, url, kind, key):
    response = client.get(url)
    if response.url != url:
        raise FetchError("dane.gov.pl catalogue redirect rejected")
    try:
        record = json.loads(response.body)["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise FetchError("dane.gov.pl catalogue schema changed") from exc
    if (record.get("type"), record.get("id")) != (kind, str(key)):
        raise FetchError("dane.gov.pl catalogue identity changed")
    return record


def check_dataset(record):
    attributes = record["attributes"]
    if (
        record["relationships"]["institution"]["data"] != {"type": "institution", "id": "100"}
        or attributes.get("license_name") != "CC BY 4.0"
        or attributes.get("license_condition_default_cc40") is not True
        or any(
            attributes.get(key)
            for key in (
                "license_condition_custom_description",
                "license_condition_db_or_copyrighted",
                "license_condition_personal_data",
                "license_condition_original",
            )
        )
    ):
        raise FetchError("GIOS dataset owner or licence changed")


def check_resource(record, spec):
    attributes = record["attributes"]
    dataset = {"type": "dataset", "id": str(spec["dataset"])}
    if (
        record["relationships"]["dataset"]["data"] != dataset
        or attributes.get("link") != spec["link"]
        or attributes.get("file_size") != spec["size"]
        or attributes.get("format") != "xlsx"
        or attributes.get("title") != spec["title"]
        or attributes.get("contains_protected_data") is not False
    ):
        raise FetchError("GIOS resource version, link or protection flag changed")


def text(value):
    return re.sub(r"\s+", " ", value).strip()


def voivodeship(value):
    name = text(value).casefold()
    # The register files spell this one name with a space; no other alias is accepted.
    name = {"kujawsko pomorskie": "kujawsko-pomorskie"}.get(name, name)
    if name not in VOIVODESHIPS:
        raise FetchError("GIOS table voivodeship invalid")
    return name


def row_number(value, expected):
    if (
        not re.fullmatch(r"[1-9][0-9]{0,3}\.?", value.strip())
        or int(value.strip().rstrip(".")) != expected
    ):
        raise FetchError("GIOS table row numbering changed")
    return expected


def count(value):
    # 2022-2023 files use "-" where 2024 uses 0; keep it as unknown, never as zero.
    if value.strip() == "-":
        return None
    if not re.fullmatch(r"0|[1-9][0-9]{0,3}", value.strip()):
        raise FetchError("GIOS accident victim count invalid")
    return int(value)


def excel_date(value, year):
    if not re.fullmatch(r"[1-9][0-9]{4}", value.strip()):
        raise FetchError("GIOS accident date is not a day serial")
    day = date(1899, 12, 30) + timedelta(days=int(value))
    if day.year != year:
        raise FetchError("GIOS accident date outside reviewed year")
    return day.isoformat()


def parse_table(key, body):
    spec = RESOURCES[key]
    rows = read_single_sheet(body)
    width = len(spec["header"])
    if not rows or rows[0][:width] != spec["header"] or any(rows[0][width:]):
        raise FetchError("GIOS table header changed")
    data = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    if rows[1 : len(data) + 1] != data:
        raise FetchError("GIOS table has blank rows inside the data")
    if not data or any(any(cell.strip() for cell in row[width:]) for row in data):
        raise FetchError("GIOS table empty or has unreviewed columns")
    records = []
    for index, row in enumerate(data, 1):
        cells = dict(zip(spec["fields"], row + [""] * width, strict=False))
        record = {
            "id": f"{spec['resource']}:{index}",
            "resource_id": spec["resource"],
            "source_row_number": row_number(cells["source_row_number"], index),
            "voivodeship": voivodeship(cells["voivodeship"]),
        }
        if spec["table"] == "register":
            record.update(
                category=spec["category"],
                as_of=spec["as_of"],
                establishment=text(cells["establishment"]),
                address=text(cells["address"]),
                safety_information_url=text(cells["safety_url"]),
            )
            if not record["establishment"] or not record["address"]:
                raise FetchError("GIOS register establishment or address missing")
        else:
            record.update(
                {
                    field: text(cells[field])
                    for field in spec["fields"][3:]
                    if not field.startswith("victims_")
                },
                year=spec["year"],
                date=excel_date(cells["date"], spec["year"]),
                victims_total=count(cells["victims_total"]),
                victims_fatal=count(cells["victims_fatal"]),
                victims_source_text=[
                    cells["victims_total"].strip(),
                    cells["victims_fatal"].strip(),
                ],
            )
            total, fatal = record["victims_total"], record["victims_fatal"]
            if (
                (total is None) != (fatal is None)
                or (total is not None and fatal > total)
                or not record["description"]
            ):
                raise FetchError("GIOS accident victims or description invalid")
        records.append(record)
    if len({record["id"] for record in records}) != len(records):
        raise FetchError("GIOS table identity duplicated")
    return records


def collect(channel, client):
    if (channel.url, channel.region, channel.kind, channel.config) != (
        URL,
        "PL",
        "table",
        CONFIG,
    ):
        raise FetchError("GIOS major-accident profile differs from reviewed scope")
    for key in sorted(DATASETS):
        check_dataset(get_json(client, f"{API}datasets/{key}", "dataset", key))
    sources, registers, accidents = [], [], []
    for key, spec in RESOURCES.items():
        check_resource(
            get_json(client, f"{API}resources/{spec['resource']}", "resource", spec["resource"]),
            spec,
        )
        response = client.get(spec["link"])
        if response.url != spec["link"] or len(response.body) != spec["size"]:
            raise FetchError("GIOS table file size or final URL changed")
        records = parse_table(key, response.body)
        (registers if spec["table"] == "register" else accidents).extend(records)
        sources.append(
            {
                "key": key,
                "dataset_id": spec["dataset"],
                "resource_id": spec["resource"],
                "title": spec["title"],
                "url": spec["link"],
                "bytes": len(response.body),
                "sha256": hashlib.sha256(response.body).hexdigest(),
                "records": len(records),
                **({"as_of": spec["as_of"]} if "as_of" in spec else {"year": spec["year"]}),
            }
        )
    data = {
        "schema": CONFIG["schema"],
        "attribution": ATTRIBUTION,
        "license": "CC BY 4.0",
        "dataset_urls": [f"https://dane.gov.pl/pl/dataset/{key}" for key in sorted(DATASETS)],
        "units": UNITS,
        "date_precision": "day",
        "sources": sources,
        "registers": registers,
        "accidents": sorted(accidents, key=lambda row: (row["date"], row["id"])),
    }
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("GIOS major-accident payload exceeds automatic budget")
    summary = {
        "kind": "gios_major_accidents",
        "collector": "gios_major_accidents",
        "scope": "PL",
        "records": len(registers) + len(accidents),
        "registers": {
            category: sum(row["category"] == category for row in registers)
            for category in ("ZDR", "ZZR")
        },
        "register_as_of": "2024-12-31",
        "accident_years": sorted({row["year"] for row in accidents}),
        "accidents": len(accidents),
        "partial": False,
        "new_api_used": False,
    }
    return payload, "application/json", summary
