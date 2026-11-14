"""Bounded official GDOŚ SDI link directory, not a geometry downloader."""

import re
from urllib.parse import parse_qsl, urlsplit

from bs4 import BeautifulSoup, Comment

from ingestion.adapters import Entry
from ingestion.transport import FetchError

URL = "https://www.gov.pl/web/gdos/dostep-do-danych-geoprzestrzennych"
CONFIG = {"listing": "gdos_services", "strict_endpoint": True}
SLUG = "registry-016-gdos-directory"
NOTE = (
    "Wykaz odnośników GDOŚ z oficjalnej strony: SDI, BDZP i gatunki inwazyjne. "
    "Tylko metadane/linki WMS/WFS/SHP, do80 pozycji, bez zapytań do usług i geometrii/ZIP. "
    "Nie zastępuje pełnej integracji Geoserwisu ani CRFOP."
)
ADDITIONAL_SERVICES = {
    f"https://bankdanych.gdos.gov.pl/backend/{service}": "Bank Danych o Zasobach Przyrodniczych"
    for service in ("wms", "wfs")
} | {
    f"https://inwazyjne.gdos.gov.pl/geoserver/{service}": "Inwazyjne gatunki obce"
    for service in ("wms", "wfs")
}
PLAIN_SERVICE_URL = re.compile(
    r"https?://(?:bankdanych|inwazyjne)\.gdos\.gov\.pl/(?:backend|geoserver)/[^\s<>\"']+"
)


def additional_service(link):
    parsed = urlsplit(link)
    return parsed.hostname in {"bankdanych.gdos.gov.pl", "inwazyjne.gdos.gov.pl"} and (
        parsed.path.startswith(("/backend/", "/geoserver/"))
    )


def endpoint_metadata(link):
    if any(char.isspace() or not char.isprintable() for char in link):
        raise FetchError("GDOŚ SDI link contains whitespace or control characters")
    parsed = urlsplit(link)
    if additional_service(link):
        if link not in ADDITIONAL_SERVICES:
            raise FetchError("GDOŚ additional service outside reviewed endpoint")
        return {
            "service": parsed.path.rsplit("/", 1)[-1].upper(),
            "operation": "service_endpoint",
            "source_link_warning": (
                "Nie potwierdzono dostępności usługi ani licencji jej danych; "
                "zapisano wyłącznie odnośnik z wykazu GDOŚ, bez pobierania map i geometrii. "
                + (
                    "Opis GDOŚ sprawdzony 2026-10-10: dane BDZP uogólnione do siatki 1 km × 1 km, "
                    "nie dokładne lokalizacje chronionych gatunków i siedlisk."
                    if parsed.hostname == "bankdanych.gdos.gov.pl"
                    else "Opis GDOŚ sprawdzony 2026-10-10: dane projektu z lat 2017–2018; "
                    "nie potwierdzono bieżących obserwacji."
                )
            ),
        }
    if (
        (parsed.scheme, parsed.netloc, parsed.path)
        not in (("https", "sdi.gdos.gov.pl", "/wms"), ("https", "sdi.gdos.gov.pl", "/wfs"))
        or parsed.fragment
        or len(link) > 1200
    ):
        raise FetchError("GDOŚ SDI link outside reviewed endpoint")
    if not parsed.query:
        return {"service": parsed.path[1:].upper(), "operation": "service_endpoint"}
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise FetchError("GDOŚ SDI query malformed") from exc
    params = {key.upper(): value for key, value in pairs}
    required = {
        "SERVICE",
        "VERSION",
        "REQUEST",
        "TYPENAME",
        "SRSNAME",
        "OUTPUTFORMAT",
        "FORMAT_OPTIONS",
    }
    if (
        parsed.path != "/wfs"
        or len(params) != len(pairs)
        or set(params) not in (required, required | {"CQL_FILTER"})
        or params.get("SERVICE") != "WFS"
        or params.get("VERSION") != "1.0.0"
        or params.get("REQUEST") != "GetFeature"
        or params.get("SRSNAME") != "EPSG:2180"
        or params.get("OUTPUTFORMAT") != "shape-zip"
        or params.get("FORMAT_OPTIONS") != "charset:windows-1250"
        or not re.fullmatch(r"GDOS:[A-Za-z][A-Za-z0-9]{0,80}", params.get("TYPENAME", ""))
    ):
        raise FetchError("GDOŚ SDI download link schema changed")
    if "CQL_FILTER" in params and (
        params["TYPENAME"] != "GDOS:PomnikiPrzyrody"
        or not re.fullmatch(
            r"kodinspire ilike 'PL\.ZIPOP\.1393\.PP\.(?:0[2468]|1[02468]|2[02468]|3[02])%'%?",
            params["CQL_FILTER"],
        )
    ):
        raise FetchError("GDOŚ regional filter outside reviewed pattern")
    warning = (
        "Odnośnik źródłowy zawiera dodatkowy znak % po filtrze regionalnym. "
        "Adres zachowano bez korekty; działanie wymaga sprawdzenia, pliku nie pobrano."
        if params.get("CQL_FILTER", "").endswith("'%")
        else ""
    )
    return {
        "service": "WFS",
        "operation": "GetFeature",
        "feature_type": params["TYPENAME"],
        "srs": params["SRSNAME"],
        "format": params["OUTPUTFORMAT"],
        "regional_filter": params.get("CQL_FILTER", ""),
        "source_link_warning": warning,
    }


def parse_directory(source, response):
    if (
        source.url != URL
        or source.config != CONFIG
        or response.url != URL
        or response.status != 200
    ):
        raise FetchError("GDOŚ directory identity changed")
    soup = BeautifulSoup(response.body, "html.parser")
    editors = soup.select(".editor-content")
    headings = [h.get_text(" ", strip=True) for h in soup.select("h2")]
    if len(editors) != 1 or headings.count("Dostęp do danych geoprzestrzennych") != 1:
        raise FetchError("GDOŚ directory content missing or ambiguous")
    entries, seen = [], set()
    links = []
    for anchor in editors[0].select("a[href]"):
        link = anchor["href"]
        if urlsplit(link).hostname == "sdi.gdos.gov.pl" or additional_service(link):
            links.append((link, anchor.get_text(" ", strip=True)))
            if len(links) > 80:
                raise FetchError("GDOŚ directory budget exceeded")
    # These four official URLs are currently plain text, not anchors. Never fetch them.
    for node in editors[0].find_all(string=True):
        if isinstance(node, Comment) or node.find_parent(["a", "script", "style"]):
            continue
        for match in PLAIN_SERVICE_URL.finditer(str(node)):
            link = match.group()
            links.append(
                (link, ADDITIONAL_SERVICES.get(link, "") + " — " + link.rsplit("/", 1)[-1].upper())
            )
            if len(links) > 80:
                raise FetchError("GDOŚ directory budget exceeded")
    for link, title in links:
        metadata = endpoint_metadata(link)
        if not title or link in seen or len(entries) >= 80:
            raise FetchError("GDOŚ directory empty title, duplicate or budget exceeded")
        seen.add(link)
        entries.append(
            Entry(
                link,
                title,
                link,
                metadata={"record_kind": "service_directory", "catalogue_url": URL, **metadata},
            )
        )
    if not {"https://sdi.gdos.gov.pl/wms", "https://sdi.gdos.gov.pl/wfs"} <= seen:
        raise FetchError("GDOŚ directory required service endpoints missing")
    return entries
