"""Reviewed PIG inventories: Geośrodowisko, hydrogeology, MWP points, CBDG; not SOPO."""

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
        "geometry": "Polygon",
        "note": (
            "Geometrie i atrybuty obiektów powierzchniowych; "
            "bez punktów obiektów górniczych i rastra spadków terenu."
        ),
    },
    # R064 beyond the heaps (2026-10-15): layer names, geometry and OBJECTID read from the
    # service JSON through WebFetch in the cloud; UNCONFIRMED until the dry-run on the PC.
    "haldypkt": {
        "path": "geosrodowisko/haldy",
        "layer": 1,
        "registry": 64,
        "title": "Hałdy - obiekty górnicze",
        "dataset": "Geośrodowisko — hałdy: obiekty górnicze",
        "limit": 100,
        "geometry": "Point",
        "note": (
            "Punkty obiektów górniczych (nazwa obiektu) powiązane z inwentaryzacją hałd; "
            "bez tabel opisowych hałd (geologia, odpady, ocena środowiskowa)."
        ),
    },
    "crgp": {
        "path": "geosrodowisko/geostanowiska",
        "layer": 0,
        "registry": 64,
        "title": "CRGP - geostanowiska",
        "dataset": "Geośrodowisko — Centralny Rejestr Geostanowisk Polski",
        "limit": 100,
        "geometry": "Point",
        "note": (
            "Punkty geostanowisk z typem, statusem, stanem ochrony, zachowania i dostępnością "
            "podanymi przez dostawcę; bez kart dokumentacyjnych i zdjęć. Stan ochrony to "
            "atrybut rejestru, nie ocena prawna."
        ),
    },
    "moek": {
        "path": "geosrodowisko/moek",
        "layer": 0,
        "registry": 64,
        "title": "MOEK - monitoring odkrywkowej eksploatacji kopalin",
        "dataset": "Geośrodowisko — MOEK: monitoring odkrywkowej eksploatacji kopalin",
        "limit": 10,
        "geometry": "Polygon",
        "note": (
            "Zasięgi powiatów z rokiem opracowania i stanem prac MOEK; "
            "bez punktów i map wyrobisk z opracowań powiatowych."
        ),
    },
    "prognozy": {
        "path": "geosrodowisko/obszary_zweryfikowane",
        "layer": 0,
        "registry": 64,
        "title": "zweryfikowane obszary prognostyczne",
        "dataset": "Geośrodowisko — zweryfikowane obszary prognostyczne kopalin",
        "limit": 50,
        "geometry": "Polygon",
        "note": (
            "Obszary prognostyczne z zasobami, nadkładem i miąższością w jednostkach "
            "dostawcy; to nie są udokumentowane złoża (MIDAS) ani koncesje."
        ),
    },
    "negatywne": {
        "path": "geosrodowisko/obszary_zweryfikowane",
        "layer": 1,
        "registry": 64,
        "title": "zweryfikowane obszary negatywnego rozpoznania",
        "dataset": "Geośrodowisko — zweryfikowane obszary negatywnego rozpoznania kopalin",
        "limit": 50,
        "geometry": "Polygon",
        "note": (
            "Obszary, w których rozpoznanie nie potwierdziło kopaliny, z powodem "
            "zakwalifikowania podanym przez dostawcę."
        ),
    },
    "jcwpd": {
        "path": "hydrogeologia/jcwpd",
        "layer": 2,
        "registry": 63,
        "title": "JCWPd - cykl planistyczny 2022-2027",
        "dataset": "JCWPd — cykl planistyczny 2022–2027",
        "limit": 10,
        "geometry": "Polygon",
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
        "geometry": "Polygon",
        "note": (
            "Wyłącznie udokumentowane GZWP; bez nieudokumentowanych GZWP "
            "i lokalnych zbiorników. Nie są to bieżące pomiary wód."
        ),
    },
    "mwp": {
        "path": "hydrogeologia/mwp",
        "layer": 0,
        "registry": 63,
        # Layer name read through Client on 2026-10-07; the cloud draft had a shorter form.
        "title": "MWP - monitoring wód podziemnych",
        "dataset": "MWP — punkty monitoringu wód podziemnych",
        "limit": 100,
        "geometry": "Point",
        "note": (
            "Wszystkie punkty sieci monitoringu wód podziemnych PIG-PIB, także już "
            "nieobserwowane, z ostatnim pomiarem zwierciadła i wydajności podanym przez "
            "dostawcę (DATA_POMIARU w formie z usługi, bez interpretacji). "
            "Bez tabel historii pomiarów i wskaźników chemicznych."
        ),
    },
    "cbdg": {
        "path": "cbdg_otwory",
        "layer": 0,
        "registry": 36,
        "title": "Otwory wiertnicze CBDG",
        "dataset": "CBDG — otwory wiertnicze",
        "limit": 100,
        "geometry": "Point",
        # 2026-10-07 [USER]: full borehole list once a month; ID budget raised only here.
        "max_ids": 400000,
        "interval": 43200,
        # Full ID manifest at the start of every call and before the last page; between
        # pages only the count, so a monthly run downloads the large manifest ~340 times.
        "pages_per_call": 10,
        "page_check": "count",
        "note": (
            "Punkty otworów wiertniczych z atrybutami z usługi (głębokość, stratygrafia, "
            "cel wiercenia, rok, archiwum); bez profili, rdzeni, dokumentów i plików. "
            "Pełny spis raz w miesiącu."
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
        or data.get("geometryType") != "esriGeometry" + p["geometry"]
        or not isinstance(fields, list)
        or [
            f.get("name")
            for f in fields
            if isinstance(f, dict) and f.get("type") == "esriFieldTypeOID"
        ]
        != ["OBJECTID"]
        # MWP returns "uniqueIdInfo": null; absent and null both mean no hashed OIDs.
        or (data.get("uniqueIdInfo") or {}).get("OIDFieldContainsHashValue", False) is not False
    ):
        raise FetchError("PIG spatial layer identity/schema changed")
