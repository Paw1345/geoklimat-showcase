"""IMGW-PIB public warnings: meteorological (registry 34) and hydrological (registry 33).

Same public API and terms as the approved IMGW measurement channels
(danepubliczne.imgw.pl/api/data, attribution required). Probe 2026-10-14T20:30Z through
the standard Client (robots checked): warningshydro returned a JSON list of 95 objects
with string values and an `obszary` list; warningsmeteo returned {"message": "Brak
ostrzeżeń meteorologicznych"} because no warning was in force. The list shape of
meteorological warnings is therefore UNCONFIRMED: any list of objects is stored and its
keys are recorded for review. The original provider bytes are stored unchanged; times are
naive provider strings (Warsaw local time UNCONFIRMED) and `data_do` 9999-12-31 means
an open-ended warning. A warning is information from IMGW, not a local alert.
"""

import hashlib
import json
from collections import Counter
from datetime import datetime

from ingestion.transport import FetchError

CONFIG_MODE = "imgw_warnings"
PRODUCTS = {"meteo": 34, "hydro": 33}
BASE = "https://danepubliczne.imgw.pl/api/data/warnings"
NAMES = {
    "meteo": "IMGW — ostrzeżenia meteorologiczne (API publiczne)",
    "hydro": "IMGW — ostrzeżenia hydrologiczne (API publiczne)",
}
ATTRIBUTION = (
    "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej "
    "– Państwowy Instytut Badawczy. Ostrzeżenia w oryginalnej postaci API; to informacja "
    "IMGW, nie lokalny alarm. Warunki ponownego wykorzystania: "
    "https://danepubliczne.imgw.pl/pl/apiinfo"
)
MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 2000
HYDRO_KEYS = {
    "opublikowano",
    "stopień",
    "data_od",
    "data_do",
    "prawdopodobienstwo",
    "numer",
    "biuro",
    "zdarzenie",
    "przebieg",
    "komentarz",
    "obszary",
}
STAMP = "%Y-%m-%d %H:%M:%S"
OPEN_ENDED = "9999-12-31 23:59:59"


def slug(product):
    return "imgw-warnings-" + product


def url(product):
    return BASE + product


def config(product):
    return {"mode": CONFIG_MODE, "product": product}


def profile(channel):
    product = channel.config.get("product")
    if (
        product not in PRODUCTS
        or channel.config != config(product)
        or channel.url != url(product)
        or channel.kind != "table"
        or channel.region != "PL"
        or channel.registry.registry_id != PRODUCTS[product]
    ):
        raise FetchError("IMGW warnings channel outside reviewed profile")
    return product


def stamp(value):
    if not isinstance(value, str):
        raise FetchError("IMGW warning date is not a string")
    try:
        datetime.strptime(value, STAMP)
    except ValueError as error:
        raise FetchError("IMGW warning date format changed") from error
    return value


def hydro_record(record):
    if not isinstance(record, dict) or not HYDRO_KEYS <= record.keys():
        raise FetchError("IMGW hydro warning fields changed")
    for key in HYDRO_KEYS - {"obszary"}:
        if not isinstance(record[key], str):
            raise FetchError("IMGW hydro warning field type changed")
    for key in ("opublikowano", "data_od", "data_do"):
        stamp(record[key])
    areas = record["obszary"]
    if not isinstance(areas, list) or not all(isinstance(area, dict) for area in areas):
        raise FetchError("IMGW hydro warning areas changed")
    return record


def parse(product, payload):
    """Validate the reviewed shapes and summarise. No writes, no HTTP."""
    if not 0 < len(payload) <= MAX_BYTES:
        raise FetchError("IMGW warnings size outside budget")
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise FetchError("IMGW warnings are not JSON") from error
    summary = {"records": 0}
    if isinstance(data, dict):
        if set(data) != {"message"} or not isinstance(data["message"], str):
            raise FetchError("IMGW warnings object changed")
        summary["provider_message"] = data["message"][:200]
        return summary
    if not isinstance(data, list) or len(data) > MAX_RECORDS:
        raise FetchError("IMGW warnings list changed")
    if not all(isinstance(record, dict) for record in data):
        raise FetchError("IMGW warnings list holds non-objects")
    summary["records"] = len(data)
    if product == "meteo":
        summary["keys_seen"] = sorted({key for record in data for key in record})[:50]
        return summary
    records = [hydro_record(record) for record in data]
    summary.update(
        events=dict(Counter(record["zdarzenie"][:80] for record in records).most_common(10)),
        levels=dict(Counter(record["stopień"] for record in records)),
        open_ended=sum(record["data_do"] == OPEN_ENDED for record in records),
        newest_published_raw=max((record["opublikowano"] for record in records), default=None),
        areas=sum(len(record["obszary"]) for record in records),
    )
    return summary


def collect(channel, client):
    product = profile(channel)
    client.max_bytes = MAX_BYTES
    response = client.get(url(product))
    payload = response.body
    parsed = parse(product, payload)
    warnings = ["times_naive_timezone_unconfirmed"]
    if product == "meteo":
        warnings.append("meteo_list_schema_unconfirmed")
    summary = {
        "kind": CONFIG_MODE,
        "scope": "PL",
        "collector": CONFIG_MODE,
        "product": product,
        "url": url(product),
        "original_response_preserved": True,
        "provider_media_type": response.headers.get("content-type", "")[:100],
        "sha256": hashlib.sha256(payload).hexdigest(),
        "semantic_status": "unconfirmed",
        "warnings": warnings,
        **parsed,
    }
    return payload, "application/json", summary
