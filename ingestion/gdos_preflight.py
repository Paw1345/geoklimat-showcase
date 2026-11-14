"""Read-only R016 preflight: robots and capabilities of the reviewed GDOŚ SDI hosts.

Only already allowlisted hosts are read; bankdanych/inwazyjne stay links (new hosts are a
user decision). No database writes, channels, layers, maps or geometry.
"""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ingestion.transport import Client, FetchError

MAX_BYTES = 2 * 1024 * 1024
TARGETS = {
    "sdi-wms": "https://sdi.gdos.gov.pl/wms?SERVICE=WMS&REQUEST=GetCapabilities&VERSION=1.3.0",
    "sdi-wfs": "https://sdi.gdos.gov.pl/wfs?SERVICE=WFS&REQUEST=GetCapabilities&VERSION=2.0.0",
    "geoserwis": "https://geoserwis.gdos.gov.pl/",
}


def plan():
    return {
        "targets": TARGETS,
        "robots": sorted({origin(url) + "/robots.txt" for url in TARGETS.values()}),
        "writes": "none",
    }


def origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def robots_report(client, url):
    """Observation of robots.txt only; the policy decision stays in Client.get."""
    try:
        response = client._request(origin(url) + "/robots.txt")
    except FetchError as exc:
        return {"error": str(exc), "status": exc.status}
    head = response.body[:200].decode("utf-8", errors="ignore").lower()
    return {
        "status": response.status,
        "content_type": response.headers.get("content-type", ""),
        "bytes": len(response.body),
        "html": "<html" in head or "<!doctype html" in head,
    }


def local(tag):
    return tag.rsplit("}", 1)[-1]


def capabilities_summary(body):
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper() or b"\x00" in body:
        raise FetchError("GDOŚ capabilities with DTD/entities rejected")
    root = ET.fromstring(body)
    texts = {}
    for node in root.iter():
        name = local(node.tag)
        if name in {"Title", "Fees", "AccessConstraints"} and name not in texts:
            texts[name] = (node.text or "").strip()[:300]
    layers = [n for n in root.iter() if local(n.tag) in {"Layer", "FeatureType"}]
    names = [
        (c.text or "").strip()
        for n in layers
        for c in n
        if local(c.tag) == "Name" and (c.text or "").strip()
    ]
    return {"root": local(root.tag), "named_layers": len(names), "sample": names[:15], **texts}


def preflight(fetch=False, client=None):
    """Default is a no-I/O plan; --fetch reads robots and the three fixed URLs."""
    if not fetch:
        return plan()
    client = client or Client()
    client.max_bytes = MAX_BYTES
    result = {"checked_at": datetime.now(UTC).isoformat(), "targets": {}}
    for key, url in TARGETS.items():
        row = {"url": url, "robots": robots_report(client, url)}
        try:
            response = client.get(url)
        except FetchError as exc:
            row.update(error=str(exc), status=exc.status)
        else:
            row.update(status=response.status, bytes=len(response.body))
            if b"Capabilities" in response.body[:2000]:
                try:
                    row["capabilities"] = capabilities_summary(response.body)
                except (ET.ParseError, FetchError) as exc:
                    row["error"] = "capabilities rejected: " + str(exc)[:200]
        result["targets"][key] = row
    return result
