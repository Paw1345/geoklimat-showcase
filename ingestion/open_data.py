"""Reviewed dane.gov.pl API 1.4 metadata window, not automatic resource downloads."""

import hashlib
import json
from datetime import timedelta
from urllib.parse import urlencode

from django.utils import timezone

from ingestion.adapters import Entry, date_value, safe_entry, text
from ingestion.transport import FetchError

URL = "https://api.dane.gov.pl/1.4/datasets"
CONFIG = {"mode": "dane_gov", "max_pages": 100}
NOTE = "Metadane zbiorów zmienionych w ostatnich 30 dniach; bez pobierania zasobów zbiorów."


def fetch_datasets(source, client):
    if source.url != URL:
        raise FetchError("Unreviewed open data API endpoint")
    now = timezone.now()
    cutoff = now - timedelta(days=30)
    previous = now + timedelta(minutes=5)
    entries, seen, fingerprints = [], set(), []
    for page in range(1, min(100, max(1, int(source.config.get("max_pages", 100)))) + 1):
        response = client.get(
            URL + "?" + urlencode({"sort": "-modified", "per_page": 100, "page": page})
        )
        data = json.loads(response.body)
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise FetchError("Open data JSON:API schema changed")
        rows = data["data"]
        if len(rows) > 100 or not isinstance(data.get("links"), dict):
            raise FetchError("Invalid open data page")
        fingerprints.append(hashlib.sha256(response.body).hexdigest())
        reached_cutoff = False
        for row in rows:
            attrs = row.get("attributes", {})
            key = row.get("id")
            if (
                row.get("type") != "dataset"
                or not isinstance(key, str)
                or not key.isdigit()
                or not attrs.get("title")
            ):
                raise FetchError("Missing dataset identity")
            stamp = date_value(attrs.get("modified"))
            if stamp is None or stamp > previous or key in seen:
                raise FetchError("Dataset order, date or repeated identity invalid")
            previous = stamp
            seen.add(key)
            if stamp < cutoff:
                reached_cutoff = True
                continue
            # Exclude download/view counters: a visit is not a dataset revision.
            stable = {
                k: attrs.get(k)
                for k in (
                    "title",
                    "notes",
                    "modified",
                    "resource_modified",
                    "license_name",
                    "license_description",
                    "license_condition_custom_description",
                    "formats",
                    "keywords",
                )
            }
            entry = safe_entry(
                Entry(
                    key,
                    attrs["title"],
                    f"https://dane.gov.pl/pl/dataset/{key}",
                    text(attrs.get("notes")),
                    attrs.get("created") or "",
                    attrs["modified"],
                    {
                        "record_kind": "dataset_collection",
                        "license": attrs.get("license_name") or "",
                        "document_hash": hashlib.sha256(
                            json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()
                        ).hexdigest(),
                        "resources_downloaded": False,
                    },
                ),
                URL,
            )
            if entry is None:
                raise FetchError("Invalid dataset metadata")
            entries.append(entry)
        if reached_cutoff or not data["links"].get("next"):
            return entries, 0, response, hashlib.sha256("".join(fingerprints).encode()).hexdigest()
        if not rows:
            raise FetchError("Empty open data page with continuation")
        # Deliberately construct the next URL; never request remote-provided URLs.
    raise FetchError("Open data page budget exceeded; no partial batch committed")
