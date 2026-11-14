"""Public static Marine STAC product catalogue; no account or asset retrieval."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from ingestion.adapters import Entry, date_value, safe_entry
from ingestion.transport import FetchError

URL = "https://stac.marine.copernicus.eu/metadata/catalog.stac.json"
CONFIG = {"mode": "marine_products"}
PARTIAL_CONFIG = {"mode": "marine_products", "incomplete_policy": "missing_metadata_v1"}
NOTE = (
    "Publiczny katalog produktów Copernicus Marine STAC: opisy, zakresy, licencje "
    "i zmiany metadanych. Bez pobierania plików, pomiarów ani prognoz."
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarineIssue:
    product_id: str
    url: str
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class MarineResult:
    entries: list[Entry]
    response: object
    digest: str
    declared_count: int
    inspected_count: int
    issues: tuple[MarineIssue, ...]
    root_sha256: str
    manifest: tuple[str, ...]

    @property
    def rejected(self):
        return len(self.issues)


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
    """Preserve the strict, four-value interface used by the ingestion service."""
    result = collect_catalogue(source, client)
    return result.entries, result.rejected, result.response, result.digest


def collect_catalogue(source, client, *, allow_partial=False, audit_partial=False):
    """Collect once; partial mode is internal and is not wired to ingestion."""
    expected_config = PARTIAL_CONFIG if audit_partial else CONFIG
    if (
        source.url != URL
        or source.config != expected_config
        or (audit_partial and not allow_partial)
    ):
        raise FetchError("Unreviewed Marine catalogue profile")
    if allow_partial and (
        source.adapter != "stac"
        or source.slug != "registry-195"
        or (audit_partial and (source.enabled or source.activation_approved))
        or (not audit_partial and (not source.enabled or not source.activation_approved))
        or (not audit_partial and not source.registry_entries.filter(registry_id=195).exists())
        or (
            audit_partial
            and list(
                source.registry_entries.order_by("registry_id").values_list(
                    "registry_id", flat=True
                )[:2]
            )
            != [195]
        )
    ):
        raise FetchError("Unreviewed Marine partial source")
    response = client.get(URL)
    root_sha256 = hashlib.sha256(response.body).hexdigest()
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
    entries, fingerprints, issues = [], [], []
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
        missing = [] if isinstance(props, dict) else ["properties"]
        missing.extend(field for field in ("title", "license") if not row.get(field))
        if allow_partial:
            if not isinstance(props, dict):
                raise FetchError("Marine product properties invalid")
            if len(url) > 1500:
                raise FetchError("Marine product URL exceeds metadata limit")
            for field in ("title", "license"):
                value = row.get(field)
                if value is not None and not isinstance(value, str):
                    raise FetchError(f"Marine product {field} type invalid")
            missing = [
                field
                for field in ("title", "license")
                if row.get(field) is None or not row[field].strip()
            ]
            for field in ("creationDate", "modifiedDate"):
                value = props.get(field)
                if value is not None and (
                    not isinstance(value, str) or (value and date_value(value) is None)
                ):
                    raise FetchError("Marine product dates invalid")
            if missing:
                issues.append(MarineIssue(key, url, tuple(missing)))
                continue
        if missing:
            raise FetchError(
                f"Marine product metadata missing ({', '.join(missing)}): {key}",
                response.status,
                url=url,
            )
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
    if not entries:
        raise FetchError("Marine catalogue has no valid products")
    return MarineResult(
        entries=entries,
        response=response,
        digest=hashlib.sha256("".join(fingerprints).encode()).hexdigest(),
        declared_count=len(links),
        inspected_count=len(links),
        issues=tuple(issues),
        root_sha256=root_sha256,
        manifest=tuple(urlsplit(url).path.split("/")[-2] for url in links),
    )
