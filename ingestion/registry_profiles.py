"""Reviewed routes. Workbook declarations are never executable configuration."""

from urllib.parse import urlencode, urlsplit

from ingestion.p0_profiles import PROFILES

LEGACY = {
    1: ["mkis"],
    5: ["gios"],
    8: ["gugik"],
    9: ["inspire-gugik"],
    12: ["gdos"],
    20: ["rcl"],
    22: ["eli-du", "eli-mp"],
    26: ["wody"],
    30: ["nfosigw"],
    61: ["gdos"],
    80: ["rdos-warszawa"],
    164: ["ec-environment"],
    166: ["ec-climate"],
    168: ["eurlex-inspire"],
    174: ["eea-news"],
    175: ["eea-news", "eea-reports"],
}


def profile(master, technical):
    key = master["ID"]
    if key == 170:
        from ingestion.cellar import CONFIG, NOTE, URL

        return ("json", URL, CONFIG.copy()), "configured", NOTE
    if key == 195:
        from ingestion.marine import CONFIG, NOTE, URL

        return ("stac", URL, CONFIG.copy()), "configured", NOTE
    from ingestion.regional_profiles import NOTE as REGIONAL_NOTE
    from ingestion.regional_profiles import PROFILES as REGIONAL

    if key in REGIONAL:
        adapter, url, config = REGIONAL[key]
        return (adapter, url, config.copy()), "configured", REGIONAL_NOTE
    if key == 184:
        from ingestion.eurostat import CONFIG, NOTE, URL

        return ("watch", URL, CONFIG.copy()), "configured", NOTE
    from ingestion.catalogue_profiles import CONFIG, NOTE
    from ingestion.catalogue_profiles import PROFILES as CATALOGUES

    if key in CATALOGUES:
        return ("stac", CATALOGUES[key], CONFIG.copy()), "configured", NOTE
    if key in PROFILES:
        url, config, note = PROFILES[key]
        return ("web", url, config.copy()), "configured", note
    status = technical["Technical status"]
    raw = master["Endpoint URL"]
    if status in {"MANUAL_AUTH_PLUS_PUBLIC", "READY_API_REGISTRATION"}:
        return (
            None,
            "requires_access",
            "Wymagana osobna zgoda i konfiguracja dostępu. Bez logowania.",
        )
    if status == "VERIFY_ENDPOINT":
        return None, "needs_endpoint", "Znana strona instytucji; brak adresu sekcji tematycznej."
    if status in {"READY_STRUCTURED", "READY_DATA_PORTAL", "READY_API_DOCS", "READY_JS_PORTAL"}:
        return (
            None,
            "needs_connector",
            "Rejestr zachowany; wymagany dedykowany publiczny interfejs i jego zakres.",
        )
    if key == 7:
        return (
            (
                "json",
                technical["Exact machine endpoint / feed"],
                {"mode": "gios", "page_size": 100, "max_pages": 20},
            ),
            "configured",
            "Lista stacji v1; nie pomiary ani alerty zdrowotne.",
        )
    if key == 21:
        return (
            (
                "json",
                technical["Exact machine endpoint / feed"],
                {"mode": "sejm", "page_size": 100, "max_pages": 30},
            ),
            "configured",
            "Procesy kadencji 10 z rejestru; paginacja ograniczona, bez interpretacji prawnej.",
        )
    if key == 24:
        from ingestion.open_data import CONFIG, NOTE, URL

        return (
            ("json", URL, CONFIG.copy()),
            "configured",
            NOTE,
        )
    if key == 191:
        return (
            ("stac", "https://stac.dataspace.copernicus.eu/v1/collections", {}),
            "configured",
            "Metadane kolekcji STAC; bez pobierania scen i plików satelitarnych.",
        )
    if key == 183:
        return (
            ("feed", "https://data.europa.eu/api/hub/search/en/feeds/datasets.atom", {}),
            "configured",
            "Atom metadanych zbiorów, tylko okno kanału.",
        )
    if key in {51, 52, 53, 54, 55}:
        # HTTPS is a candidate and must pass its own live audit, never a silent fallback.
        url = technical["Exact machine endpoint / feed"].replace("http://", "https://", 1)
        if key == 54:
            return (
                ("feed", url, {}),
                "configured",
                "Kandydat HTTPS dla ATOM z arkusza; wymaga testu.",
            )
        protocol = {51: "WMS", 52: "WMTS", 53: "WFS", 55: "CSW"}[key]
        url += "?" + urlencode({"SERVICE": protocol, "REQUEST": "GetCapabilities"})
        return (
            ("ogc", url, {"service": protocol}),
            "configured",
            "Monitoring GetCapabilities; nie kopia danych przestrzennych.",
        )
    if status in {"READY_FEED_API", "READY_FEED_OR_API", "READY_OGC", "READY_RSS", "READY_DERIVED"}:
        return (
            None,
            "needs_endpoint",
            "Katalog, szablon adresu lub kanał pochodny; potrzebne konkretne powiązanie.",
        )
    if status == "READY_HTML" and urlsplit(raw).scheme == "https":
        return (
            ("web", raw, {}),
            "configured",
            "Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
        )
    return (
        None,
        "needs_endpoint",
        "Adres HTTP lub brak obsługiwanego publicznego endpointu. Bez automatycznej aktywacji.",
    )
