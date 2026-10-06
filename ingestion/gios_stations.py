"""Approved single-page active-station catalogue. No measurements or child requests."""

import hashlib
import json
import math

from ingestion.adapters import Entry
from ingestion.transport import (
    GIOS_POLICY_EXCEPTION,
    GIOS_STATIONS_REQUEST,
    GIOS_STATIONS_URL,
    FetchError,
)

CONFIG = {
    "mode": "gios_stations_v1",
    "strict_endpoint": True,
    "robots_policy_exception": GIOS_POLICY_EXCEPTION,
}
LEGACY_CONFIG = {"mode": "gios", "max_pages": 20, "page_size": 100}
NOTE = (
    "Lista aktywnych stacji GIOŚ, maksymalnie500/jedna strona, co60min. "
    "Źródło danych: GIOŚ - EKOINFONET. Metadane stacji, nie wyniki pomiarów. "
    "Zatwierdzony wyjątek polityki API wyłącznie dla błędnego robots302 do strony HTTP."
)
TERMS = "https://powietrze.gios.gov.pl/pjp/content/terms_of_service"
DOCS = "https://powietrze.gios.gov.pl/pjp/content/api"


def parse_stations(source, response):
    if (
        source.adapter != "json"
        or source.url != GIOS_STATIONS_URL
        or source.config != CONFIG
        or response.url != GIOS_STATIONS_REQUEST
        or response.status != 200
    ):
        raise FetchError("GIOS station profile/endpoint changed")
    try:
        data = json.loads(response.body.decode("utf-8"))
        rows = data["Lista stacji pomiarowych"]
        if (
            not isinstance(rows, list)
            or not 1 <= len(rows) <= 500
            or type(data["totalPages"]) is not int
            or data["totalPages"] != 1
            or data["links"]["self"] != GIOS_STATIONS_REQUEST
            or data["links"]["first"] != GIOS_STATIONS_REQUEST
            or data["links"]["last"] != GIOS_STATIONS_REQUEST
            or data["meta"]["dc:title"] != "Lista stacji pomiarowych"
            or data["meta"]["schema:provider"]["name"] != "Główny Inspektorat Ochrony Środowiska"
        ):
            raise FetchError("GIOS station catalogue incomplete or identity changed")
        # API declares next=page1 even on its only page. It is never fetched.
        if data["links"]["next"] != GIOS_STATIONS_REQUEST.replace("page=0", "page=1"):
            raise FetchError("GIOS station next-link schema changed")
        entries, identities, codes = [], set(), set()
        for row in rows:
            identity, name, code = (
                row["Identyfikator stacji"],
                row["Nazwa stacji"],
                row["Kod stacji"],
            )
            if (
                type(identity) is not int
                or identity <= 0
                or identity in identities
                or not isinstance(name, str)
                or not 1 <= len(name) <= 1000
                or not isinstance(code, str)
                or not 1 <= len(code) <= 100
                or code in codes
            ):
                raise FetchError("GIOS station row identity changed")
            latitude, longitude = row["WGS84 φ N"], row["WGS84 λ E"]
            if (
                not isinstance(latitude, str)
                or not isinstance(longitude, str)
                or not all(math.isfinite(float(v)) for v in (latitude, longitude))
                or not 48 <= float(latitude) <= 56
                or not 13 <= float(longitude) <= 26
            ):
                raise FetchError("GIOS station coordinates invalid")
            fields = ["Nazwa miasta", "Gmina", "Powiat", "Województwo", "Ulica"]
            if any(
                row.get(field) is not None
                and (not isinstance(row[field], str) or len(row[field]) > 500)
                for field in fields
            ):
                raise FetchError("GIOS station locality schema changed")
            city_id = row.get("Identyfikator miasta")
            if city_id is not None and (type(city_id) is not int or city_id <= 0):
                raise FetchError("GIOS station city identity invalid")
            entries.append(
                Entry(
                    str(identity),
                    name,
                    "https://powietrze.gios.gov.pl/pjp/current/station_details/info/"
                    + str(identity),
                    "Źródło danych: GIOŚ - EKOINFONET. Metadane stacji; nie bieżący pomiar.",
                    metadata={
                        "record_kind": "metadata",
                        "listing_scope": "active_station_catalogue",
                        "document_hash": hashlib.sha256(
                            json.dumps(row, sort_keys=True, ensure_ascii=False).encode()
                        ).hexdigest(),
                        "station_id": identity,
                        "station_code": code,
                        "latitude_raw": latitude,
                        "longitude_raw": longitude,
                        "station_locality": {field: row.get(field) for field in fields},
                        "city_id": city_id,
                        "source_attribution": "Źródło danych: GIOŚ - EKOINFONET",
                        "access_policy": response.access_policy,
                        "api_documentation": DOCS,
                        "source_terms": TERMS,
                        "measurement_values_collected": False,
                        "full_document_downloaded": False,
                    },
                )
            )
            identities.add(identity)
            codes.add(code)
        return entries
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise FetchError("GIOS station schema or encoding changed") from exc


def fetch_stations(source, client):
    if source.config != CONFIG or source.url != GIOS_STATIONS_URL or source.interval_minutes < 60:
        raise FetchError("GIOS station approved profile/schedule changed")
    response = client.get(GIOS_STATIONS_REQUEST, gios_station_exception=True)
    from ingestion.adapters import parse

    try:
        entries, rejected = parse(source, response)
    except FetchError as exc:
        exc.retry_after = max(3600, exc.retry_after)
        raise
    return entries, rejected, response, hashlib.sha256(response.body).hexdigest()
