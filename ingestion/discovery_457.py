"""Read-only discovery for 4.5.7 sources without a reviewed interface (EDO, EFFIS, ESA).

One bounded GET per candidate through the standard Client (robots, size cap, retry).
Candidate URLs are UNCONFIRMED until this probe runs from the user's PC; hosts are
allowed only for the probe process and are not added to the permanent allowlist.
"""

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from django.conf import settings
from django.test.utils import override_settings

from ingestion.transport import Client, FetchError

PROBES = {
    200: [
        ("html", "https://drought.emergency.copernicus.eu/"),
    ],
    202: [
        ("html", "https://forest-fire.emergency.copernicus.eu/"),
        (
            "ogc",
            "https://maps.effis.emergency.copernicus.eu/effis"
            "?service=WMS&request=GetCapabilities&version=1.3.0",
        ),
    ],
    204: [
        ("json", "https://eocat.esa.int/eo-catalogue/collections?limit=10"),
    ],
}
HOSTS = {
    "drought.emergency.copernicus.eu",
    "forest-fire.emergency.copernicus.eu",
    "maps.effis.emergency.copernicus.eu",
    "effis.jrc.ec.europa.eu",
    "eocat.esa.int",
}
OFFICIAL = (".copernicus.eu", ".europa.eu", ".esa.int")
INTERFACE = re.compile(
    r"wms|wfs|wcs|ows|api|download|stac|csv|geojson|\.zip|\.nc\b|\.tiff?\b|data",
    re.IGNORECASE,
)
MAX_LINKS = 60
MAX_LAYERS = 100
MAX_FOLLOW = 3


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def interface_links(base, body):
    parser = Links()
    parser.feed(body.decode("utf-8", "replace"))
    found = []
    for href in parser.hrefs:
        url = urljoin(base, href).split("#")[0]
        host = urlsplit(url).hostname or ""
        if (
            url.startswith("https://")
            and host.endswith(OFFICIAL)
            and INTERFACE.search(url)
            and url not in found
        ):
            found.append(url)
        if len(found) >= MAX_LINKS:
            break
    return found


def local(tag):
    return tag.rsplit("}", 1)[-1]


def capabilities(body):
    document = body.decode("utf-8", "replace")
    if "\x00" in document or "<!DOCTYPE" in document.upper() or "<!ENTITY" in document.upper():
        raise FetchError("Unsafe XML document")
    root = ET.fromstring(document)
    service = next((el for el in root if local(el.tag) == "Service"), None)
    layers = []
    for layer in root.iter():
        if local(layer.tag) != "Layer":
            continue
        fields = {local(child.tag): (child.text or "").strip() for child in layer}
        if fields.get("Name"):
            layers.append({"name": fields["Name"], "title": fields.get("Title", "")})
    return {
        "root": local(root.tag),
        "version": root.get("version"),
        "title": next(
            ((el.text or "").strip() for el in service or [] if local(el.tag) == "Title"), None
        ),
        "fees": next(
            ((el.text or "").strip() for el in service or [] if local(el.tag) == "Fees"), None
        ),
        "access_constraints": next(
            (
                (el.text or "").strip()
                for el in service or []
                if local(el.tag) == "AccessConstraints"
            ),
            None,
        ),
        "layer_count": len(layers),
        "layers": layers[:MAX_LAYERS],
    }


def json_summary(body):
    data = json.loads(body)
    if not isinstance(data, dict):
        return {"type": type(data).__name__}
    collections = data.get("collections")
    return {
        "keys": sorted(data)[:30],
        "collections_returned": len(collections) if isinstance(collections, list) else None,
        "number_matched": data.get("numberMatched"),
        "ids": [c.get("id") for c in collections[:20] if isinstance(c, dict)]
        if isinstance(collections, list)
        else [],
        "licenses": sorted({str(c.get("license")) for c in collections if isinstance(c, dict)})[:20]
        if isinstance(collections, list)
        else [],
        "next": any(
            isinstance(link, dict) and link.get("rel") == "next" for link in data.get("links", [])
        ),
    }


def probe(client, kind, url):
    row = {"kind": kind, "url": url}
    try:
        response = client.get(url)
    except FetchError as exc:
        return {**row, "status": "blocked", "error": str(exc)[:300], "http": exc.status}
    row.update(
        status="ok",
        http=response.status,
        final_url=response.url,
        media=response.headers.get("content-type", "").split(";")[0],
        bytes=len(response.body),
    )
    try:
        if kind == "html":
            row["links"] = interface_links(response.url, response.body)
        elif kind == "ogc" and row["media"] == "text/html":
            # Live 2026-10-08: an EDO "WMS" link answered with a viewer page; list its
            # interface links instead of reporting the HTML as unsafe XML.
            row.update(status="html_page", links=interface_links(response.url, response.body))
        elif kind == "ogc":
            row["capabilities"] = capabilities(response.body)
        else:
            row["summary"] = json_summary(response.body)
    except (ValueError, ET.ParseError, FetchError) as exc:
        row.update(status="unparsed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
    return row


def discover(registries=None, client=None):
    hosts = sorted(set(settings.INGEST_ALLOWED_HOSTS) | HOSTS)
    with override_settings(INGEST_ALLOWED_HOSTS=hosts):
        client = client or Client()
        client.max_bytes = settings.DATA_AUTO_MAX_BYTES
        results = {}
        for registry, probes in PROBES.items():
            if registries and registry not in registries:
                continue
            rows = [probe(client, kind, url) for kind, url in probes]
            # Follow at most a few advertised OGC endpoints on the probe hosts only.
            follow = [
                link
                for row in rows
                for link in row.get("links", [])
                if re.search(r"wms|wfs|ows", link, re.IGNORECASE)
                and urlsplit(link).hostname in HOSTS
            ][:MAX_FOLLOW]
            for link in follow:
                separator = "&" if "?" in link else "?"
                rows.append(
                    probe(client, "ogc", f"{link}{separator}service=WMS&request=GetCapabilities")
                )
            results[registry] = rows
    return {
        "scope": "read_only_interface_discovery",
        "registries": results,
        "database_writes": False,
        "permanent_allowlist_changed": False,
    }
