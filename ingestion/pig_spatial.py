"""Reviewed polygon inventories; not live groundwater measurements or full SOPO."""

from ingestion.pig_data import TERMS, read
from ingestion.transport import FetchError

EVIDENCE = "https://baza.pgi.gov.pl/geoportal/uslugi/gis"
BASE = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/"
PROFILES = {
    "haldy": {
        "path": "geosrodowisko/haldy",
        "layer": 2,
        "registry": 64,
        "title": "Hałdy - obiekty powierzchniowe",
        "dataset": "Geośrodowisko — hałdy powierzchniowe",
        "limit": 50,
        "note": (
            "Geometrie i atrybuty obiektów powierzchniowych; "
            "bez punktów obiektów górniczych i rastra spadków terenu."
        ),
    },
    "jcwpd": {
        "path": "hydrogeologia/jcwpd",
        "layer": 2,
        "registry": 63,
        "title": "JCWPd - cykl planistyczny 2022-2027",
        "dataset": "JCWPd — cykl planistyczny 2022–2027",
        "limit": 10,
        "note": (
            "Warstwa cyklu 2022–2027, nie warstwa przyszłego cyklu 2028–2033. "
            "Atrybuty stanu oznaczone 2019 pozostają historyczne, nie są bieżącymi pomiarami."
        ),
    },
    "gzwp": {
        "path": "hydrogeologia/gzwp",
        "layer": 0,
        "registry": 63,
        "title": "GZWP udokumentowane",
        "dataset": "GZWP — udokumentowane zbiorniki",
        "limit": 10,
        "note": (
            "Wyłącznie udokumentowane GZWP; bez nieudokumentowanych GZWP "
            "i lokalnych zbiorników. Nie są to bieżące pomiary wód."
        ),
    },
}


def config(key):
    p = PROFILES[key]
    return {
        "mode": "pig_spatial_scan",
        "pagination": "pig_ids",
        "profile": key,
        "layer": p["layer"],
        "feature_limit": p["limit"],
    }


def channel_url(key):
    p = PROFILES[key]
    return BASE + p["path"] + "/MapServer/" + str(p["layer"]) + "/query"


def slug(key):
    return "pig-" + key + "-scan"


def attribution(key):
    p = PROFILES[key]
    return (
        "PIG-PIB / "
        + p["dataset"]
        + ". "
        + p["note"]
        + " Przypięty spis OBJECTID; eksport jednej strony, nie jednoczasowa wersja całego zbioru. "
        "Dostawca przelicza do WGS84. Retencja30dni dotyczy kopii, nie wieku danych. "
        "Czas wytworzenia niepotwierdzony. Warunki: " + TERMS
    )


def profile(channel):
    key = channel.config.get("profile")
    p = PROFILES.get(key)
    if (
        not p
        or channel.config != config(key)
        or channel.url != channel_url(key)
        or channel.kind != "geojson"
        or channel.region != "PL"
    ):
        raise FetchError("PIG spatial scan outside reviewed profile")
    return p


def check_schema(channel, client):
    p = profile(channel)
    data = read(client, channel.url.removesuffix("/query") + "?f=json")
    fields = data.get("fields")
    if (
        data.get("name") != p["title"]
        or data.get("geometryType") != "esriGeometryPolygon"
        or not isinstance(fields, list)
        or [
            f.get("name")
            for f in fields
            if isinstance(f, dict) and f.get("type") == "esriFieldTypeOID"
        ]
        != ["OBJECTID"]
        or data.get("uniqueIdInfo", {}).get("OIDFieldContainsHashValue", False) is not False
    ):
        raise FetchError("PIG spatial layer identity/schema changed")
