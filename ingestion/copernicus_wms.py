"""Copernicus EDO (200) and EFFIS (202) WMS layer catalogues; no maps or rasters are fetched.

Interfaces read from the user's PC on 2026-10-08 (discover_satellite_crisis): EFFIS WMS 1.3.0
at maps.effis.emergency.copernicus.eu (67 named layers, Fees "none", AccessConstraints
"None"); the EDO viewer page advertises drought.emergency.copernicus.eu/api/wms (WMS 1.1.1),
whose capabilities were verified on 2026-10-09. Each named layer becomes one
record with its time extent and an unfetched Poland preview link (GetMap).
"""

import hashlib
import re
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

NOTE = (
    "Metadane WMS: opis usługi i nazwanych warstw z zakresem dat oraz link podglądu mapy "
    "dla Polski (niepobierany). Bez map, rastrów, wartości i plików; rastry tylko na żądanie. "
    "Wartości dla PL i produkty plikowe pozostają otwarte w 4.5.7."
)
PROFILES = {
    "edo": {
        "url": "https://drought.emergency.copernicus.eu/api/wms"
        "?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.1.1",
        "version": "1.1.1",
        "name": "Copernicus EDO — warstwy WMS suszy (katalog)",
        "organization": "Copernicus EMS / JRC — European Drought Observatory",
        "registry_id": 200,
        "evidence": "https://drought.emergency.copernicus.eu/data/wms-service",
        "terms": "https://drought.emergency.copernicus.eu/terms&conditions",
    },
    "effis": {
        "url": "https://maps.effis.emergency.copernicus.eu/effis"
        "?service=WMS&request=GetCapabilities&version=1.3.0",
        "version": "1.3.0",
        "name": "Copernicus EFFIS — warstwy WMS pożarów (katalog)",
        "organization": "Copernicus EMS / JRC — European Forest Fire Information System",
        "registry_id": 202,
        "evidence": "https://forest-fire.emergency.copernicus.eu/applications/data-and-services",
        "terms": "https://forest-fire.emergency.copernicus.eu/about-effis/data-license",
    },
}
MAX_BYTES = 5 * 1024 * 1024
MAX_LAYERS = 200
MAX_TIME_VALUES = 20000
# Poland with a small margin, EPSG:4326 (lon 14.0–24.2, lat 49.0–55.0).
POLAND = (14.0, 49.0, 24.2, 55.0)
# WMS 1.1.1 capabilities usually carry this external DTD declaration; expat never loads it.
# This external form and EDO's reviewed empty vendor element are stripped, never loaded.
DTD_111 = re.compile(r'<!DOCTYPE WMT_MS_Capabilities SYSTEM "https?://[^"\[\]<>]{1,200}"\s*>')
DTD_EDO_111 = re.compile(
    r"<!DOCTYPE WMT_MS_Capabilities SYSTEM "
    r'"http://schemas\.opengis\.net/wms/1\.1\.1/WMS_MS_Capabilities\.dtd"\s*'
    r"\[\s*<!ELEMENT VendorSpecificCapabilities EMPTY>\s*\]\s*>"
)
ROOTS = {"1.1.1": "WMT_MS_Capabilities", "1.3.0": "{http://www.opengis.net/wms}WMS_Capabilities"}


def slug(key):
    return f"registry-{PROFILES[key]['registry_id']}-{key}-wms"


def config(key):
    return {"mode": "copernicus_wms", "profile": key, "service": "WMS"}


def local(tag):
    return tag.rsplit("}", 1)[-1]


def child(node, name):
    return next((el for el in node if local(el.tag) == name), None)


def child_text(node, name):
    found = child(node, name)
    return (found.text or "").strip() if found is not None else ""


def time_extent(raw):
    """Summarise points and start/end[/period] ranges in source order, without guessing dates."""
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values or len(values) > MAX_TIME_VALUES:
        raise FetchError("WMS time extent missing or over budget")
    if any(len(value) > 80 or not value.isprintable() for value in values):
        raise FetchError("WMS time value format changed")
    parts = [value.split("/") for value in values]
    if any(len(value) > 3 or any(not part.strip() for part in value) for value in parts):
        raise FetchError("WMS time value format changed")
    first, last = parts[0], parts[-1]
    return {
        "time_first": first[0][:40],
        "time_last": (last[1] if len(last) > 1 else last[0])[:40],
        "time_values": len(values),
        "time_intervals": sum(1 for value in parts if len(value) > 1),
    }


def layer_time(layer, version):
    if version == "1.3.0":
        for node in layer:
            if local(node.tag) == "Dimension" and (node.get("name") or "").lower() == "time":
                return node.text or "", node.get("default") or ""
    else:
        for node in layer:
            if local(node.tag) == "Extent" and (node.get("name") or "").lower() == "time":
                return node.text or "", node.get("default") or ""
    return None


def layer_crs(layer, version):
    name = "CRS" if version == "1.3.0" else "SRS"
    codes = set()
    for node in layer:
        if local(node.tag) == name and node.text:
            codes.update(code.upper() for code in node.text.split())
    return codes


def preview_url(profile, name, version, time_default):
    west, south, east, north = POLAND
    params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": version, "LAYERS": name}
    if version == "1.3.0":
        # WMS 1.3.0 EPSG:4326 uses latitude/longitude axis order.
        params.update(CRS="EPSG:4326", BBOX=f"{south},{west},{north},{east}")
    else:
        params.update(SRS="EPSG:4326", BBOX=f"{west},{south},{east},{north}")
    params.update(STYLES="", WIDTH="800", HEIGHT="600", FORMAT="image/png", TRANSPARENT="TRUE")
    if time_default:
        params["TIME"] = time_default
    return profile["url"].split("?", 1)[0] + "?" + urlencode(params)


def parse_capabilities(source, response):
    key = source.config.get("profile") if isinstance(source.config, dict) else None
    profile = PROFILES.get(key)
    if (
        not profile
        or source.config != config(key)
        or source.url != profile["url"]
        or response.url != profile["url"]
        or response.status != 200
        or len(response.body) > MAX_BYTES
    ):
        raise FetchError("Copernicus WMS identity or size policy changed")
    try:
        document = response.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FetchError("Copernicus WMS encoding rejected") from exc
    if profile["version"] == "1.1.1":
        # Official EDO response, 2026-10-09: one EMPTY element, no entities or other DTDs.
        document = DTD_EDO_111.sub("", document, count=1)
        document = DTD_111.sub("", document, count=1)
    if "\x00" in document or "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
        raise FetchError("Copernicus WMS XML entities/encoding rejected")
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise FetchError("Copernicus WMS invalid XML") from exc
    version = profile["version"]
    if root.tag != ROOTS[version] or root.get("version") != version:
        raise FetchError("Copernicus WMS namespace/version changed")
    service, capability = child(root, "Service"), child(root, "Capability")
    if service is None or capability is None:
        raise FetchError("Copernicus WMS service/capability missing")
    title = text(child_text(service, "Title"))
    if not title:
        raise FetchError("Copernicus WMS service identity missing")
    common = {
        "record_kind": "service_capabilities",
        "service": "WMS",
        "version": version,
        "evidence_url": profile["evidence"],
        "terms_url": profile["terms"],
        "access_constraints_raw": child_text(service, "AccessConstraints")[:500],
        "fees_raw": child_text(service, "Fees")[:200],
        "resources_downloaded": False,
    }
    layers = []
    # Depth-first walk; WMS children inherit time dimensions and CRS from parent layers.
    stack = [
        (node, None, frozenset())
        for node in reversed(list(capability))
        if local(node.tag) == "Layer"
    ]
    while stack:
        layer, inherited_time, inherited_crs = stack.pop()
        if len(layers) >= MAX_LAYERS:
            raise FetchError("Copernicus WMS layer budget changed")
        own_time = layer_time(layer, version)
        current_time = own_time if own_time is not None else inherited_time
        crs = inherited_crs | layer_crs(layer, version)
        layers.append((layer, current_time, crs))
        children = [node for node in layer if local(node.tag) == "Layer"]
        stack.extend((node, current_time, crs) for node in reversed(children))
    entries = [
        Entry(
            source.url,
            title,
            source.url,
            text(child_text(service, "Abstract"))[:2000],
            metadata={
                **common,
                "document_hash": hashlib.sha256(ET.tostring(root)).hexdigest(),
                "layers_count": len(layers),
            },
        )
    ]
    seen = set()
    for layer, time, crs in layers:
        name = child_text(layer, "Name")
        if not name:  # Unnamed grouping layer is not a requestable layer.
            continue
        layer_title = text(child_text(layer, "Title"))
        if len(name) > 150 or not name.isprintable() or name in seen or not layer_title:
            raise FetchError("Copernicus WMS named layer missing or repeated")
        seen.add(name)
        metadata = {**common, "layer_name": name}
        default = ""
        if time is not None:
            metadata.update(time_extent(time[0]))
            default = time[1][:40]
            metadata["time_default"] = default
        if "EPSG:4326" in crs:
            metadata["preview_url"] = preview_url(profile, name, version, default)
        # Hash the layer description and its time extent: a new date step is an update.
        metadata["document_hash"] = hashlib.sha256(
            ET.tostring(layer) + repr(sorted(metadata.items())).encode()
        ).hexdigest()
        entries.append(
            Entry(
                source.url + "#layer=" + name,
                layer_title,
                metadata.get("preview_url", source.url),
                text(child_text(layer, "Abstract"))[:2000],
                metadata=metadata,
            )
        )
    if not seen:
        raise FetchError("Copernicus WMS has no named layers")
    return entries
