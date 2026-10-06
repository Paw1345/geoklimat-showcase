"""Public static Marine STAC product catalogue; no account or asset retrieval."""

import hashlib
import json
import logging
import re
from urllib.parse import urljoin, urlsplit

from ingestion.adapters import Entry, date_value, safe_entry
from ingestion.transport import FetchError

URL = "https://stac.marine.copernicus.eu/metadata/catalog.stac.json"
CONFIG = {"mode": "marine_products"}
NOTE = (
    "Publiczny katalog produktów Copernicus Marine STAC: opisy, zakresy, licencje "
    "i zmiany metadanych. Bez pobierania plików, pomiarów ani prognoz."
)
logger = logging.getLogger(__name__)


def product_url(href):
    if not isinstance(href, str):
        raise FetchError("Marine product link missing")
    url = urljoin(URL, href)
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "stac.marine.copernicus.eu"
        or parts.query
        or parts.fragment
        or not re.fullmatch(r"/metadata/[A-Za-z0-9_-]+/product\.stac\.json", parts.path)
    ):
        raise FetchError("Marine link left reviewed product catalogue")
    return url


def fetch_catalogue(source, client):
    if source.url != URL or source.config != CONFIG:
        raise FetchError("Unreviewed Marine catalogue profile")
    response = client.get(URL)
    data = json.loads(response.body)
    if (
        response.url != URL
        or not isinstance(data, dict)
        or data.get("type") != "Catalog"
        or data.get("id") != "MDS"
    ):
        raise FetchError("Marine root catalogue schema changed")
    if not isinstance(data.get("links"), list) or any(
        not isinstance(link, dict) for link in data["links"]
    ):
        raise FetchError("Marine root links schema changed")
    if any(link.get("rel") == "next" for link in data["links"]):
        raise FetchError("Unexpected Marine root pagination")
    links = [product_url(link.get("href")) for link in data["links"] if link.get("rel") == "child"]
    if not 1 <= len(links) <= 500 or len(set(links)) != len(links):
        raise FetchError("Marine product catalogue empty, duplicate or above budget")
    entries, fingerprints = [], []
    for index, url in enumerate(links, 1):
        response = client.get(url)
        row = json.loads(response.body)
        key = urlsplit(url).path.split("/")[-2]
        if (
            response.url != url
            or not isinstance(row, dict)
            or row.get("type") != "Collection"
            or row.get("id") != key
        ):
            raise FetchError("Marine product identity or endpoint changed")
        props = row.get("properties")
        if not isinstance(props, dict) or not row.get("title") or not row.get("license"):
            raise FetchError("Marine product metadata missing")
        published, updated = props.get("creationDate") or "", props.get("modifiedDate") or ""
        if any(value and date_value(value) is None for value in (published, updated)):
            raise FetchError("Marine product dates invalid")
        stable = {
            field: row.get(field)
            for field in (
                "id",
                "title",
                "description",
                "license",
                "extent",
                "keywords",
                "links",
                "sci:doi",
            )
        }
        # Do not store contacts, thumbnails or marketing/use-case changes.
        stable["properties"] = {
            field: props.get(field)
            for field in (
                "creationDate",
                "modifiedDate",
                "formats",
                "updateFrequencies",
                "geoResolution",
            )
        }
        fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        fingerprints.append(fingerprint)
        entry = safe_entry(
            Entry(
                key,
                row["title"],
                url,
                row.get("description") or "",
                published,
                updated,
                {
                    "record_kind": "dataset_collection",
                    "document_hash": fingerprint,
                    "product_id": key,
                    "license": row["license"],
                    "extent": row.get("extent"),
                    "resources_downloaded": False,
                },
            ),
            URL,
        )
        if entry is None:
            raise FetchError("Invalid Marine product metadata")
        entries.append(entry)
        if index % 50 == 0:
            logger.info("Marine catalogue: %s/%s product metadata read", index, len(links))
    return entries, 0, response, hashlib.sha256("".join(fingerprints).encode()).hexdigest()
