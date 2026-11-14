"""GDOŚ Central Register of Nature Protection Forms (CRFOP, registry 15) from dane.gov.pl.

Dataset 471 publishes eight register tables "stan na dzień 4 lutego 2026 r.", CC0 1.0.
The files carry an .xls name but are XLSX (ZIP) containers; they are read with the
XLSX reader. Each file is pinned by resource id, link, size and SHA-256. One form
spans one row plus continuation rows that add legal acts or conventions only.
Attribute-only register: no geometry. Units absent from headers stay UNCONFIRMED.
"""

import hashlib
import json
import re
from datetime import date

from django.conf import settings

from ingestion.gios_major_accidents import API, MEDIA, get_json
from ingestion.transport import FetchError
from ingestion.xlsx_table import read_single_sheet

DATASET = 471
REGISTRY_ID = 15
URL = f"https://dane.gov.pl/pl/dataset/{DATASET}"
AS_OF = "2026-02-04"
ATTRIBUTION = (
    "Generalna Dyrekcja Ochrony Środowiska — Centralny Rejestr Form Ochrony Przyrody; "
    "dane.gov.pl zbiór 471, CC0 1.0 według metadanych katalogu; stan na 4.02.2026. "
    "Dane przetworzone: wiersze kontynuacji połączone z formą, tekst bez zmian. "
    "Rejestr atrybutowy bez geometrii; nie zastępuje usług SDI GDOŚ."
)
ACT_COLUMNS = [
    "rodzaj_aktu_nazwa",
    "akt_prawny_nazwa",
    "akt_prawny_oznaczenie",
    "akt_prawny_miejesce_pub",
    "akt_prawny_data_pub",
    "akt_prawny_obowiazuje_do",
]
CONVENTION_COLUMNS = ["rodzaj_konw_nazwa", "konwencja_opis", "konwencja_rok"]
DATE_COLUMNS = {"data_utworzenia", "akt_prawny_data_pub", "akt_prawny_obowiazuje_do"}
FLAG_COLUMNS = {"archiwalny", "konwencja", "otulina", "plan_ochrony", "zadania_ochronne"}
AREA_COLUMNS = {"powierzchnia", "powierzchnia_ot"}
TITLE = "Centralny Rejestr Form Ochrony Przyrody - {}"


def _register(rid, file, size, sha, label, parts=1):
    return {
        "resource": rid,
        "link": f"{MEDIA}20260204/{file}_-_stan_na_04.02.2026.xls",
        "size": size,
        "sha256": sha,
        "title": TITLE.format(label),
        "parts": parts,
    }


REGISTERS = {
    "parki-narodowe": _register(
        1187255,
        "parki_narodowe",
        43330,
        "b827723ea1372907539eb8f80c33949a56450682a7c09bb0e61bebd920ab12e7",
        "parki narodowe",
    ),
    "rezerwaty-przyrody": _register(
        1187256,
        "rezerwaty_przyrody",
        662108,
        "3c2ff06435512cf24d9b8e5ca030e77cc990d3c1401bdc017985b253d4caccad",
        "rezerwaty przyrody",
    ),
    "parki-krajobrazowe": _register(
        1187245,
        "parki_krajobrazowe",
        275489,
        "a92647d945cef43ec9e1e767bb5cefa38c11b4869dc7e80a3e1a79ab24ccc5e3",
        "parki krajobrazowe",
    ),
    "obszary-chronionego-krajobrazu": _register(
        1187240,
        "obszary_chronionego_krajobrazu",
        408710,
        "1d87b83a3684fc4f69aecd3f5062ead12d0d9bbe1d3fbf131111cc4714911a5b",
        "obszary chronionego krajobrazu",
    ),
    "obszary-natura-2000": _register(
        1187243,
        "obszary_Natura_2000",
        246990,
        "c45f36d350d5b50f2c99cde0179169211936ec46bf1914d8ca89cb2848f17d93",
        "obszary Natura 2000",
    ),
    "stanowiska-dokumentacyjne": _register(
        1187261,
        "stanowiska_dokumentacyjne",
        69744,
        "334f7a7c6fdc539cb1b4629908ef792af3b4215c9da5af63c2848e6511b5b649",
        "stanowiska dokumentacyjne",
    ),
    "zespoly-przyrodniczo-krajobrazowe": _register(
        1187265,
        "zespo%C5%82y_przyrodniczo-krajobrazowe",
        119269,
        "a7017f9caa13920d68ed5c1c14131bec075887350e3639cff664b02c324cd6c8",
        "zespoły przyrodniczo-krajobrazowe",
    ),
    # ~8 MB as JSON: split deterministically into three channels by sorted fop_id.
    "uzytki-ekologiczne": _register(
        1187262,
        "u%C5%BCytki_ekologiczne",
        1266723,
        "35800d1409f6b7b6d29f21ef655bd26ae1cb9cbc2e103ae9aaae661576e759b1",
        "użytki ekologiczne",
        parts=3,
    ),
}


def profiles():
    for key, spec in REGISTERS.items():
        for part in range(1, spec["parts"] + 1):
            yield profile(key, part)


def profile(register, part=1):
    spec = REGISTERS.get(register)
    if not spec or not 1 <= part <= spec["parts"]:
        raise FetchError("Unknown reviewed CRFOP register")
    config = {"mode": "crfop", "register": register}
    slug = f"crfop-{register}"
    if spec["parts"] > 1:
        config["part"] = part
        slug += f"-czesc-{part}"
    return {"slug": slug, "url": URL, "config": config}


def check_dataset(record):
    attributes = record["attributes"]
    if (
        record["relationships"]["institution"]["data"] != {"type": "institution", "id": "23"}
        or attributes.get("license_name") != "CC0 1.0"
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
        raise FetchError("CRFOP dataset owner or licence changed")


def check_resource(record, spec):
    attributes = record["attributes"]
    if (
        record["relationships"]["dataset"]["data"] != {"type": "dataset", "id": str(DATASET)}
        or attributes.get("link") != spec["link"]
        or attributes.get("file_size") != spec["size"]
        or attributes.get("format") != "xls"
        or attributes.get("title") != spec["title"]
        or attributes.get("contains_protected_data") is not False
        # Descriptions mix plain and non-breaking spaces (parki narodowe uses U+00A0).
        or "stan na dzień 4 lutego 2026"
        not in re.sub(r"\s+", " ", attributes.get("description") or "")
    ):
        raise FetchError("CRFOP resource version, link or protection flag changed")


def validate_value(column, value):
    if not value:
        return
    if column in DATE_COLUMNS:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise FetchError("CRFOP date invalid") from exc
    elif column in FLAG_COLUMNS and value not in ("t", "f"):
        raise FetchError("CRFOP flag invalid")
    elif column in AREA_COLUMNS and not re.fullmatch(r"\d+(\.\d+)?", value):
        raise FetchError("CRFOP area invalid")
    elif column == "konwencja_rok" and not re.fullmatch(r"\d{4}", value):
        raise FetchError("CRFOP convention year invalid")


def parse_register(spec, body):
    if len(body) != spec["size"] or hashlib.sha256(body).hexdigest() != spec["sha256"]:
        raise FetchError("CRFOP file differs from the reviewed version")
    rows = read_single_sheet(body, max_rows=20000, max_columns=60, max_unpacked=16 * 1024 * 1024)
    if not rows:
        raise FetchError("CRFOP register empty")
    header = rows[0]
    if (
        header[:6]
        != ["fop_id", "inspire_id", "typ_formy_nazwa", "archiwalny", "nazwa", "data_utworzenia"]
        or len(set(header)) != len(header)
        or not set(ACT_COLUMNS) <= set(header)
        or not set(CONVENTION_COLUMNS) <= set(header)
    ):
        raise FetchError("CRFOP header changed")
    data = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    if rows[1 : len(data) + 1] != data or any(
        any(c.strip() for c in r[len(header) :]) for r in data
    ):
        raise FetchError("CRFOP blank rows inside the data or unreviewed columns")
    act_index = [header.index(name) for name in ACT_COLUMNS]
    convention_index = [header.index(name) for name in CONVENTION_COLUMNS]
    extra = set(act_index) | set(convention_index)
    form_columns = [name for i, name in enumerate(header) if i and i not in extra]
    forms, seen = [], set()
    for row in data:
        row = [cell.strip() for cell in row] + [""] * (len(header) - len(row))
        for column, value in zip(header, row, strict=False):
            validate_value(column, value)
        act = [row[i] for i in act_index]
        convention = [row[i] for i in convention_index]
        if row[0]:
            if (
                not re.fullmatch(r"[1-9][0-9]{0,8}", row[0])
                or row[0] in seen
                # Names may be blank in the source (many unnamed ecological sites).
                or not row[1].startswith("PL.ZIPOP.1393.")
            ):
                raise FetchError("CRFOP form identity invalid")
            seen.add(row[0])
            forms.append(
                {
                    "id": int(row[0]),
                    "values": [row[header.index(name)] for name in form_columns],
                    "legal_acts": [],
                    "conventions": [],
                }
            )
        elif not forms or any(row[i] for i in range(len(header)) if i not in extra):
            raise FetchError("CRFOP continuation row carries form attributes")
        if any(act):
            forms[-1]["legal_acts"].append(act)
        if any(convention):
            forms[-1]["conventions"].append(convention)
    forms.sort(key=lambda form: form["id"])
    return {
        "form_columns": form_columns,
        "act_columns": ACT_COLUMNS,
        "convention_columns": CONVENTION_COLUMNS,
        "forms": forms,
    }


def collect(channel, client):
    config = channel.config
    register, part = config.get("register"), config.get("part", 1)
    try:
        expected = profile(register, part)
    except FetchError:
        expected = None
    if not expected or (channel.url, channel.region, channel.kind, config) != (
        URL,
        "PL",
        "table",
        expected["config"],
    ):
        raise FetchError("CRFOP profile differs from reviewed scope")
    spec = REGISTERS[register]
    check_dataset(get_json(client, f"{API}datasets/{DATASET}", "dataset", DATASET))
    check_resource(
        get_json(client, f"{API}resources/{spec['resource']}", "resource", spec["resource"]),
        spec,
    )
    response = client.get(spec["link"])
    if response.url != spec["link"]:
        raise FetchError("CRFOP file final URL changed")
    table = parse_register(spec, response.body)
    total = len(table["forms"])
    # Deterministic contiguous slices of the sorted forms; the file itself is pinned.
    forms = [
        form
        for index, form in enumerate(table["forms"])
        if index * spec["parts"] // total + 1 == part
    ]
    data = {
        "schema": "crfop-register-v1",
        "register": register,
        "part": part,
        "parts": spec["parts"],
        "as_of": AS_OF,
        "license": "CC0 1.0",
        "attribution": ATTRIBUTION,
        "resource_id": spec["resource"],
        "url": spec["link"],
        "sha256": spec["sha256"],
        "units": {
            column: {"unit": None, "unit_status": "UNCONFIRMED"}
            for column in table["form_columns"]
            if column in AREA_COLUMNS
        },
        "source_flags_note": "Flagi t/f (np. archiwalny) zachowane bez interpretacji.",
        "forms_total_in_register": total,
        **{key: table[key] for key in ("form_columns", "act_columns", "convention_columns")},
        "forms": forms,
    }
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > settings.DATA_AUTO_MAX_BYTES:
        raise FetchError("CRFOP payload exceeds automatic budget")
    summary = {
        "kind": "crfop",
        "collector": "crfop",
        "register": register,
        "part": part,
        "parts": spec["parts"],
        "as_of": AS_OF,
        "scope": "PL",
        "records": len(forms),
        "forms_total_in_register": total,
        "legal_acts": sum(len(form["legal_acts"]) for form in forms),
        "units_unconfirmed": sorted(data["units"]),
        "partial": spec["parts"] > 1,
    }
    return payload, "application/json", summary
