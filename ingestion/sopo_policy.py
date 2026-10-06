"""Conservative hold for SOPO automated retrieval; no local override grants access."""

import posixpath
from urllib.parse import unquote, urlsplit

TERMS_URL = (
    "https://www.pgi.gov.pl/osuwiska/dokumenty-prawne/"
    "8549-regulamin-korzystania-z-zasobow-sopo/file.html"
)
ERROR = "SOPO automated retrieval requires provider authorization"
DETAIL = (
    "Regulamin SOPO z 2025-04-29, §5h, zabrania automatycznego pobierania informacji "
    "i danych. Monitoring zatrzymany do wyjaśnienia z PIG-PIB; zapisane metadane "
    "są historyczne. Lokalna zgoda operatora ani poprawny robots.txt nie znoszą ograniczenia."
)


def blocked_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path
    for _ in range(5):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = posixpath.normpath(path.replace("\\", "/")).lower()
    if host in {"mapa.osuwiska.pgi.gov.pl", "osuwiska.pgi.gov.pl"}:
        return True
    if host in {"pgi.gov.pl", "www.pgi.gov.pl"}:
        return (path == "/osuwiska" or path.startswith("/osuwiska/")) and not path.startswith(
            "/osuwiska/dokumenty-prawne/"
        )
    return host == "cbdgmapa.pgi.gov.pl" and any(
        path.startswith(prefix + "/sopo")
        for prefix in (
            "/arcgis/services/geozagrozenia",
            "/arcgis/rest/services/geozagrozenia",
        )
    )


def blocked_source(source):
    return blocked_url(source.url) or (
        source.config.get("mode") == "pig_wms" and source.config.get("profile") == "sopo"
    )
