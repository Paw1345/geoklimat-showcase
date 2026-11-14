"""GUGiK WFS service catalogue: GetCapabilities descriptions only, no features fetched."""

import hashlib
from xml.etree import ElementTree as ET

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

ORIGIN = "https://mapy.geoportal.gov.pl"
QUERY = "?SERVICE=WFS&REQUEST=GetCapabilities"
EVIDENCE = "https://www.geoportal.gov.pl/pl/usluga/uslugi-pobierania-wfs/"
NOTE = (
    "Katalog usług WFS GUGiK z oficjalnego wykazu: opis usługi i typów obiektów "
    "z GetCapabilities, bez pobierania obiektów. Zmiana opisu nie potwierdza zmiany danych. "
    "Dane obiektów z wybranych usług wymagają osobnej decyzji zakresu."
)
# Official list, 2026-10-15 (45 services); wfsGN already has its own data channel.
SERVICES = {
    "prg-jednostki": (
        "PRG — jednostki terytorialne",
        "/wss/service/PZGIK/PRG/WFS/AdministrativeBoundaries",
    ),
    "prg-adresy": ("PRG — adresy i ulice", "/wss/ext/wfs/KrajowaIntegracjaNumeracjiAdresowej"),
    "prng": (
        "Państwowy Rejestr Nazw Geograficznych",
        "/wss/service/PZGiK/PRNG/WFS/GeographicalNames",
    ),
    "bdot-stat-budynki": (
        "BDOT10k — statystyki budynków i budowli",
        "/wss/service/PZGIK/BDOT10k/WFS/StatystykiBudynkow",
    ),
    "bdot-stat-komunikacja": (
        "BDOT10k — statystyki sieci komunikacyjnej",
        "/wss/service/PZGIK/BDOT10k/WFS/StatystykiSieciKomunikacyjnej",
    ),
    "bdot-stat-woda": (
        "BDOT10k — statystyki sieci wodnej",
        "/wss/service/PZGIK/BDOT10k/WFS/StatystykiSieciWodnej",
    ),
    "bdot-stat-inne": (
        "BDOT10k — statystyki obiektów innych",
        "/wss/service/PZGIK/BDOT10k/WFS/StatystykiObiektowInnych",
    ),
    "bdot-nowe-budynki": ("BDOT10k — nowe budynki", "/wss/service/PZGIK/BDOT10k/WFS/NoweBudynki"),
    "turbiny-strefy": (
        "Strefy wyłączone wokół turbin wiatrowych",
        "/wss/service/PZGIK/BDOT10k/WFS/MasztyStrefyWylaczone",
    ),
    "strefa-buforowa-bialorus": (
        "Strefa buforowa przy granicy z Białorusią",
        "/wss/ext/KGSG/CzasowyZakazPrzebywania",
    ),
    "przejazdy-kolejowe": ("Przejazdy kolejowe", "/wss/service/wfs/sdi/Przejazdy"),
    "osnowa-pozioma": (
        "Podstawowa osnowa geodezyjna pozioma",
        "/wss/service/PZGIK/PodstawowaOsnowaGeodezyjnaPozioma",
    ),
    "osnowa-wysokosciowa": (
        "Podstawowa osnowa geodezyjna wysokościowa",
        "/wss/service/PZGIK/PodstawowaOsnowaGeodezyjnaWysokosciowa",
    ),
    "osnowa-grawimetryczna": ("Osnowa grawimetryczna", "/wss/service/PZGIK/OsnowaGrawimetryczna"),
    "osnowa-magnetyczna": ("Osnowa magnetyczna", "/wss/service/PZGIK/OsnowaMagnetyczna"),
    "siatki-arkuszowe": (
        "Siatki podziału arkuszowego",
        "/wss/service/PZGIK/mapy/WFS/SiatkiPodzialuArkuszowego",
    ),
    "siatki-orto-1992": (
        "Siatki rozszerzone ORTO (PL-1992)",
        "/wss/service/PZGIK/ORTO/WFS/SiatkiRozszerzonegoPodzialuArkuszowego1992",
    ),
    "siatki-nmt-1992": (
        "Siatki rozszerzone NMT (PL-1992)",
        "/wss/service/PZGIK/NMT/WFS/SiatkiRozszerzonegoPodzialuArkuszowego1992",
    ),
    "fotopunkty": ("Osnowa fotogrametryczna", "/wss/service/PZGIK/ZDJ/WFS/Fotopunkty"),
    "aerotriangulacja": ("Aerotriangulacja", "/wss/service/PZGIK/ZDJ/WFS/Aerotriangulacja"),
    "linie-mozaikowania": ("Linie mozaikowania", "/wss/service/PZGIK/ZDJ/WFS/LinieMozaikowania"),
    "modele-3d": ("Modele siatkowe 3D", "/wss/service/PZGIK/NMT/WFS/ModeleSiatkowe3D"),
    "skor-orto": ("Skorowidze ortofotomapy", "/wss/service/PZGIK/ORTO/WFS/Skorowidze"),
    "skor-true-orto": (
        "Skorowidze prawdziwej ortofotomapy",
        "/wss/service/PZGIK/ORTO/WFS/SkorowidzPrawdziwejOrtofotomapy",
    ),
    "skor-zdjecia": (
        "Skorowidze zdjęć lotniczych — środki rzutów",
        "/wss/service/PZGIK/ZDJ/WFS/Skorowidze_Srodki_Rzutow_Zdjec",
    ),
    "skor-lidar-kron86": (
        "Skorowidze LIDAR (PL-KRON86-NH)",
        "/wss/service/PZGIK/DanePomiaroweLidarKRON86/WFS/Skorowidze",
    ),
    "skor-lidar-evrf2007": (
        "Skorowidze LIDAR (PL-EVRF2007-NH)",
        "/wss/service/PZGIK/DanePomiaroweLidarEVRF2007/WFS/Skorowidze",
    ),
    "skor-nmt-kron86": (
        "Skorowidze NMT (PL-KRON86-NH)",
        "/wss/service/PZGIK/NumerycznyModelTerenuKRON86/WFS/Skorowidze",
    ),
    "skor-nmt-evrf2007": (
        "Skorowidze NMT (PL-EVRF2007-NH)",
        "/wss/service/PZGIK/NumerycznyModelTerenuEVRF2007/WFS/Skorowidze",
    ),
    "skor-nmpt-kron86": (
        "Skorowidze NMPT (PL-KRON86-NH)",
        "/wss/service/PZGIK/NumerycznyModelPokryciaTerenuKRON86/WFS/Skorowidze",
    ),
    "skor-nmpt-evrf2007": (
        "Skorowidze NMPT (PL-EVRF2007-NH)",
        "/wss/service/PZGIK/NumerycznyModelPokryciaTerenuEVRF2007/WFS/Skorowidze",
    ),
    "skor-bdot10k": ("Skorowidze BDOT10k", "/wss/service/PZGIK/BDOT/WFS/PobieranieBDOT10k"),
    "skor-intensywnosc": (
        "Skorowidze obrazów intensywności",
        "/wss/service/PZGIK/OI/WFS/SkorowidzeObrazowIntensywnosci",
    ),
    "skor-orto-ukosne": (
        "Skorowidze ortofotomapy ukośnej",
        "/wss/service/PZGIK/ORTO/WFS/skorOrtoUkosne",
    ),
    "inspire-au": ("INSPIRE — jednostki administracyjne", "/wss/service/wfsAU/guest"),
    "inspire-ad": ("INSPIRE — adresy", "/wss/service/INSPIRE/Addresses"),
    "inspire-tn": ("INSPIRE — sieci transportowe", "/wss/service/wfsTN/guest"),
    "inspire-oi": ("INSPIRE — ortoobrazy", "/wss/service/wfsOI/guest"),
    "inspire-el": ("INSPIRE — ukształtowanie terenu", "/wss/service/wfsEL/guest"),
    "inspire-lcv": ("INSPIRE — użytkowanie terenu", "/wss/service/wfsLCV/guest"),
    "inspire-bu": ("INSPIRE — budynki", "/wss/service/wfsBU/guest"),
    "inspire-pf": ("INSPIRE — obiekty produkcyjne i przemysłowe", "/wss/service/wfsPF/guest"),
    "inspire-us": ("INSPIRE — usługi użyteczności publicznej", "/wss/service/wfsUS/guest"),
    "inspire-am": ("INSPIRE — strefy ograniczone i regulacyjne", "/wss/service/wfsAM/guest"),
}
MAX_FEATURE_TYPES = 500
MAX_BYTES = 5 * 1024 * 1024
WFS_NAMESPACES = {"http://www.opengis.net/wfs/2.0", "http://www.opengis.net/wfs"}


def slug(key):
    return "registry-053-gugik-wfs-" + key


def url(key):
    return ORIGIN + SERVICES[key][1] + QUERY


def config(key):
    return {"mode": "gugik_wfs", "profile": key, "service": "WFS"}


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def children(element, name):
    return [child for child in element if local(child) == name]


def child_text(element, name):
    found = children(element, name)
    if len(found) > 1:
        raise FetchError("GUGiK WFS repeated element " + name)
    return (found[0].text or "") if found else ""


def parse_capabilities(source, response):
    key = source.config.get("profile")
    if (
        key not in SERVICES
        or source.config != config(key)
        or source.url != url(key)
        or response.url != url(key)
        or response.status != 200
        or len(response.body) > MAX_BYTES
    ):
        raise FetchError("GUGiK WFS identity or budget changed")
    try:
        document = response.body.decode("utf-8")
        if "\x00" in document or "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
            raise FetchError("GUGiK WFS XML entities/encoding rejected")
        # Parse bytes: providers send an XML declaration with encoding.
        root = ET.fromstring(response.body)
    except (ET.ParseError, UnicodeDecodeError, ValueError) as exc:
        raise FetchError("GUGiK WFS invalid XML") from exc
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if local(root) != "WFS_Capabilities" or namespace not in WFS_NAMESPACES:
        raise FetchError("GUGiK WFS namespace changed")
    version = root.get("version", "")
    identification = children(root, "ServiceIdentification") or children(root, "Service")
    lists = children(root, "FeatureTypeList")
    if len(identification) != 1 or len(lists) != 1:
        raise FetchError("GUGiK WFS service or feature list missing or ambiguous")
    service = identification[0]
    title = text(child_text(service, "Title"))
    if not title:
        raise FetchError("GUGiK WFS service title missing")
    types = children(lists[0], "FeatureType")
    if not types or len(types) > MAX_FEATURE_TYPES:
        raise FetchError("GUGiK WFS feature type budget or schema changed")
    common = {
        "record_kind": "service_capabilities",
        "service": "WFS",
        "version": version,
        "catalogue_title": SERVICES[key][0],
        "evidence_url": EVIDENCE,
        "access_constraints_raw": child_text(service, "AccessConstraints"),
        "fees_raw": child_text(service, "Fees"),
        "features_downloaded": False,
    }
    entries = [
        Entry(
            source.url,
            title,
            source.url,
            text(child_text(service, "Abstract")),
            metadata={
                **common,
                "document_hash": hashlib.sha256(ET.tostring(root)).hexdigest(),
                "feature_types_count": len(types),
            },
        )
    ]
    seen = set()
    for feature_type in types:
        name = child_text(feature_type, "Name").strip()
        type_title = text(child_text(feature_type, "Title")) or name
        if (
            not name
            or len(name) > 200
            or any(not c.isprintable() or c.isspace() for c in name)
            or name in seen
        ):
            raise FetchError("GUGiK WFS feature type name missing or repeated")
        seen.add(name)
        entries.append(
            Entry(
                source.url + "#type=" + name,
                type_title,
                source.url,
                text(child_text(feature_type, "Abstract")),
                metadata={
                    **common,
                    "feature_type": name,
                    "default_crs": (
                        child_text(feature_type, "DefaultCRS")
                        or child_text(feature_type, "DefaultSRS")
                        or child_text(feature_type, "SRS")
                    ).strip()[:200],
                    "document_hash": hashlib.sha256(ET.tostring(feature_type)).hexdigest(),
                },
            )
        )
    return entries
