"""Reviewed PIG WMS descriptions; no maps, features or files are fetched."""

import hashlib
from xml.etree import ElementTree as ET

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

NS = {"w": "http://www.opengis.net/wms"}
QUERY = "?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
NOTE = (
    "Metadane WMS1.3.0: opis usługi i nazwanych warstw, bez map, geometrii, pomiarów i plików. "
    "Monitorowanie zmian opisu nie potwierdza zmian zawartości danych. "
    "Pełny konektor pozostaje needs_connector."
)
PROFILES = {
    "geosrodowisko": {
        "url": "https://cbdgmapa.pgi.gov.pl/arcgis/services/geosrodowisko/haldy/MapServer/WMSServer"
        + QUERY,
        "name": "PIG — Geośrodowisko: odpady wydobywcze WMS",
        "registry_ids": (36, 37, 64),
        "evidence": "https://baza.pgi.gov.pl/geoportal/uslugi/gis",
    },
    "midas": {
        "url": "https://cbdgmapa.pgi.gov.pl/arcgis/services/midas/MapServer/WMSServer" + QUERY,
        "name": "PIG — MIDAS: usługa i warstwy WMS",
        "registry_ids": (36, 37, 65),
        "evidence": "https://www.pgi.gov.pl/midas/dane-przestrzenne.html",
    },
    "sopo": {
        "url": "https://cbdgmapa.pgi.gov.pl/arcgis/services/geozagrozenia/sopo_obszary/MapServer/WMSServer"
        + QUERY,
        "name": "PIG — SOPO: usługa i warstwy WMS",
        "registry_ids": (36, 37, 62),
        "evidence": "https://baza.pgi.gov.pl/geoportal/uslugi/gis",
    },
    "hydro": {
        "url": "https://epsh.pgi.gov.pl/zasoby-obbil-wms/service.svc/get" + QUERY,
        "name": "PIG — hydrogeologia: obszary bilansowe WMS",
        "registry_ids": (36, 37, 63),
        "evidence": "https://baza.pgi.gov.pl/geoportal/uslugi/gis",
    },
}


def slug(key):
    return "registry-036-pig-" + key + "-wms"


def config(key):
    return {"mode": "pig_wms", "profile": key, "service": "WMS"}


def parse_capabilities(source, response):
    key = source.config.get("profile")
    profile = PROFILES.get(key)
    if (
        not profile
        or source.config != config(key)
        or source.url != profile["url"]
        or response.url != profile["url"]
        or response.status != 200
        or len(response.body) > 5 * 1024 * 1024
        or b"<!DOCTYPE" in response.body.upper()
        or b"<!ENTITY" in response.body.upper()
    ):
        raise FetchError("PIG WMS identity or XML policy changed")
    try:
        document = response.body.decode("utf-8")
        if "\x00" in document or "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
            raise FetchError("PIG WMS XML entities/encoding rejected")
        root = ET.fromstring(document)
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise FetchError("PIG WMS invalid XML") from exc
    if root.tag != "{http://www.opengis.net/wms}WMS_Capabilities" or root.get("version") != "1.3.0":
        raise FetchError("PIG WMS namespace/version changed")
    services = root.findall("w:Service", NS)
    capabilities = root.findall("w:Capability", NS)
    if len(services) != 1 or len(capabilities) != 1:
        raise FetchError("PIG WMS service/capability missing or ambiguous")
    service = services[0]
    title = text(service.findtext("w:Title", namespaces=NS))
    if service.findtext("w:Name", namespaces=NS) != "WMS" or not title:
        raise FetchError("PIG WMS service identity missing")
    layers = capabilities[0].findall(".//w:Layer", NS)
    if not layers or len(layers) > 200:
        raise FetchError("PIG WMS layer budget or schema changed")
    common = {
        "record_kind": "service_capabilities",
        "service": "WMS",
        "version": "1.3.0",
        "evidence_url": profile["evidence"],
        "access_constraints_raw": service.findtext(
            "w:AccessConstraints", default="", namespaces=NS
        ),
        "fees_raw": service.findtext("w:Fees", default="", namespaces=NS),
        "resources_downloaded": False,
    }
    entries = [
        Entry(
            source.url,
            title,
            source.url,
            text(service.findtext("w:Abstract", namespaces=NS)),
            metadata={
                **common,
                "document_hash": hashlib.sha256(ET.tostring(root)).hexdigest(),
                "layers_count": len(layers),
            },
        )
    ]
    seen = set()
    for layer in layers:
        names = layer.findall("w:Name", NS)
        if not names:  # Unnamed grouping layer is not a requestable layer.
            continue
        name = names[0].text or ""
        layer_title = text(layer.findtext("w:Title", namespaces=NS))
        if (
            len(names) != 1
            or not name
            or len(name) > 150
            or any(not c.isprintable() or c.isspace() for c in name)
            or name in seen
            or not layer_title
        ):
            raise FetchError("PIG WMS named layer missing or repeated")
        seen.add(name)
        entries.append(
            Entry(
                source.url + "#layer=" + name,
                layer_title,
                source.url,
                text(layer.findtext("w:Abstract", namespaces=NS)),
                metadata={
                    **common,
                    "layer_name": name,
                    "document_hash": hashlib.sha256(ET.tostring(layer)).hexdigest(),
                },
            )
        )
    if not seen:
        raise FetchError("PIG WMS has no named layers")
    return entries
