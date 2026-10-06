"""BDL collection descriptions only; never follow provider template links."""

import hashlib
import json
import re

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

ORIGIN = "https://ogcapi.bdl.lasy.gov.pl"
URL = ORIGIN + "/collections?f=json"
SLUG = "registry-039-bdl-collections"
CONFIG = {"mode": "bdl_collections", "strict_endpoint": True}
EVIDENCE = "https://www9.bdl.lasy.gov.pl/portal/uslugi-ogc?v=1"
NOTE = (
    "Katalog kolekcji OGC API Features BDL: opisy warstw, bez geometrii i plików. "
    "Zmiana opisu nie potwierdza zmiany danych. Pełny konektor pozostaje needs_connector."
)


def parse_catalogue(source, response):
    if (
        source.adapter != "json"
        or source.url != URL
        or source.config != CONFIG
        or response.url != URL
        or response.status != 200
        or len(response.body) > 5 * 1024 * 1024
    ):
        raise FetchError("BDL catalogue identity or budget changed")
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FetchError("BDL catalogue invalid JSON") from exc
    if not isinstance(data, dict):
        raise FetchError("BDL catalogue schema changed")
    rows, links = data.get("collections"), data.get("links")
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 100
        or not isinstance(links, list)
        or any(not isinstance(link, dict) or link.get("rel") == "next" for link in links)
        or sum(link.get("rel") == "self" and link.get("href") == URL for link in links) != 1
    ):
        raise FetchError("BDL catalogue pagination, identity or count changed")
    entries, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise FetchError("BDL collection schema changed")
        key, title, description = row.get("id"), row.get("title"), row.get("description", "")
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", key)
            or key in seen
            or not isinstance(title, str)
            or not text(title)
            or not isinstance(description, str)
            or row.get("itemType") != "feature"
        ):
            raise FetchError("BDL collection identity missing or repeated")
        seen.add(key)
        url = ORIGIN + "/collections/" + key + "?f=json"
        row_links = row.get("links")
        if (
            not isinstance(row_links, list)
            or any(not isinstance(link, dict) for link in row_links)
            or sum(link.get("rel") == "self" and link.get("href") == url for link in row_links) != 1
        ):
            raise FetchError("BDL collection self link missing or changed")
        # Ignore canonical/alternate/download links (live catalogue contains demo URLs).
        # Retain only the descriptive fields used here, not a full provider payload.
        descriptive = {"id": key, "title": title, "description": description}
        entries.append(
            Entry(
                key,
                title,
                url,
                description,
                metadata={
                    "record_kind": "dataset_catalogue",
                    "collection_id": key,
                    "service": "OGC API Features",
                    "evidence_url": EVIDENCE,
                    "document_hash": hashlib.sha256(
                        json.dumps(descriptive, sort_keys=True).encode()
                    ).hexdigest(),
                    "resources_downloaded": False,
                    "geometry_downloaded": False,
                    "reuse_permission_confirmed": False,
                    "full_dataset_coverage": False,
                    "provider_links_ignored": True,
                },
            )
        )
    return entries
