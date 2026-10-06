"""Documented F&T GET reference catalogues, never calls, attachments or legal facts."""

import hashlib
import json
from datetime import datetime
from urllib.parse import urlsplit

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

BASE = "https://ec.europa.eu/info/funding-tenders/opportunities/"
PROGRAMMES = {"LIFE2027": "LIFE 2021–2027", "HORIZON": "Horizon Europe 2021–2027"}
NOTE = (
    "Katalog metadanych dokumentów referencyjnych LIFE i Horizon Europe 2021–2027. "
    "Nie lista aktualnych naborów, terminów ani ocen prawnych. Bez pobierania plików, "
    "tłumaczeń i poprzednich wersji; pełna integracja Funding & Tenders nadal otwarta."
)
HOSTS = {"ec.europa.eu", "eur-lex.europa.eu", "environment.ec.europa.eu", "eic.ec.europa.eu"}


def profile(code):
    if code not in PROGRAMMES:
        raise FetchError("Funding programme outside reviewed profile")
    return {
        "slug": "registry-189-reference-" + code.lower(),
        "url": BASE + f"static-content/reference-documents/{code}.json?lang=en",
        "config": {"mode": "funding_reference", "programme": code, "language": "en"},
    }


def checked_text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FetchError("Funding reference text schema changed")
    return value.strip()


def fetch_catalogue(source, client):
    p = profile(source.config.get("programme"))
    if (source.adapter, source.url, source.config) != ("json", p["url"], p["config"]):
        raise FetchError("Funding reference outside reviewed profile")
    response = client.get(source.url, headers={"Accept": "application/json"})
    if response.status != 200 or response.url != source.url:
        raise FetchError("Funding reference response identity changed")
    try:
        rows = json.loads(response.body)
    except ValueError as exc:
        raise FetchError("Funding reference invalid JSON") from exc
    entries, seen = [], set()
    count = 0

    def walk(nodes, path=(), parent_visible=True):
        nonlocal count
        if not isinstance(nodes, list) or len(path) > 6:
            raise FetchError("Funding reference tree schema or depth changed")
        for node in nodes:
            count += 1
            if count > 1000 or not isinstance(node, dict) or type(node.get("visible")) is not bool:
                raise FetchError("Funding reference node schema or budget exceeded")
            label = checked_text(node.get("label"), 1000)
            category = (*path, label)
            visible = parent_visible and node["visible"]
            if node.get("urlExternal"):
                url = checked_text(node["urlExternal"], 1500)
                try:
                    parsed = urlsplit(url)
                    valid = (
                        parsed.scheme == "https"
                        and parsed.netloc in HOSTS
                        and not parsed.username
                        and not parsed.password
                    )
                except ValueError:
                    valid = False
                metadata = node.get("metadata")
                if not valid or not isinstance(metadata, dict):
                    raise FetchError("Funding reference link or metadata changed")
                raw = metadata.get("published", "")
                if not isinstance(raw, str):
                    raise FetchError("Funding reference publication date schema changed")
                if raw and raw != "Not published yet":
                    try:
                        datetime.strptime(raw, "%d-%m-%Y")
                    except ValueError as exc:
                        raise FetchError("Funding reference publication date invalid") from exc
                identity = json.dumps([source.config["programme"], category, url])
                if identity in seen:
                    raise FetchError("Funding reference duplicate catalogue identity")
                seen.add(identity)
                if visible:
                    entry = safe_entry(
                        Entry(
                            identity,
                            label,
                            url,
                            " / ".join(category),
                            metadata={
                                "record_kind": "dataset_catalogue",
                                "programme": source.config["programme"],
                                "category_path": list(category),
                                "reference_published_raw": raw,
                                "reference_unpublished": raw == "Not published yet",
                                "reference_metadata": metadata,
                                "catalogue_url": source.url,
                                "catalogue_scope": NOTE,
                                "attachments_downloaded": False,
                            },
                        ),
                        source.url,
                    )
                    if not entry:
                        raise FetchError("Funding reference invalid entry")
                    entries.append(entry)
            walk(node.get("children", []), category, visible)

    walk(rows)
    if not entries or len(entries) > 500:
        raise FetchError("Funding reference empty or record budget exceeded")
    entries.sort(key=lambda entry: entry.key)
    digest = hashlib.sha256("".join(e.digest() for e in entries).encode()).hexdigest()
    return entries, 0, response, digest
