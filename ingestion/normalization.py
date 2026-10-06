"""Conservative identities. Never drop substantive query parameters or legal versions."""

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


def clean_text(value):
    value = BeautifulSoup(str(value or ""), "html.parser").get_text(" ")
    return " ".join(unicodedata.normalize("NFC", value).split())


def title_key(value):
    value = clean_text(value).casefold()
    # Keep numbers and diacritics; punctuation/spacing do not identify separate publications.
    return " ".join(re.findall(r"\w+", value, flags=re.UNICODE))


def canonical_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Invalid public document URL")
    host = parsed.hostname.lower().encode("idna").decode("ascii")
    port = parsed.port
    if port and (parsed.scheme.lower(), port) not in (("http", 80), ("https", 443)):
        host += f":{port}"
    pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "msclkid"}
    ]
    # Query order, duplicate parameters, path case and trailing slash may be meaningful.
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", urlencode(pairs), ""))


def normalized_date(value, language="pl"):
    if not value:
        return None, "unknown"
    value = value.strip()
    for parse in (
        datetime.fromisoformat,
        parsedate_to_datetime,
        lambda raw: datetime.strptime(raw, "%d.%m.%Y"),
    ):
        try:
            result = parse(value)
            precision = (
                "day" if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}", value) else "time"
            )
            if result.tzinfo is None:
                result = result.replace(
                    tzinfo=ZoneInfo("Europe/Warsaw") if language == "pl" else UTC
                )
            return result.astimezone(UTC), precision
        except (ValueError, TypeError):
            continue
    return None, "unknown"


def normalized_fields(item):
    metadata = item.metadata or {}
    # Publisher is the institution responsible for the source, not a guessed human author.
    authors = metadata.get("authors", [])
    if not isinstance(authors, list):
        authors = [authors]
    authors = sorted(
        {
            clean_text(author)[:200]
            for author in authors
            if isinstance(author, str) and clean_text(author)
        }
    )
    title = title_key(item.title)[:1000]
    raw_date = metadata.get("publication_date_raw", "")
    published, precision = normalized_date(raw_date, item.source.language)
    if not raw_date and item.published_at:
        published, precision = item.published_at, "time"
    duplicate_key = ""
    summary = title_key(item.summary)
    if (
        item.source.adapter in {"html", "feed"}
        and published
        and len(title) >= 40
        and len(summary) >= 160
        and not metadata.get("ELI")
    ):
        day = published.astimezone(ZoneInfo("Europe/Warsaw")).date().isoformat()
        duplicate_key = hashlib.sha256(
            f"{item.source.language}\n{day}\n{title}\n{summary}".encode()
        ).hexdigest()
    return {
        "canonical_url": canonical_url(item.url),
        "normalized_title": title,
        "publisher": clean_text(item.source.organization)[:120],
        "authors": authors,
        "published_at": published,
        "date_precision": precision,
        "duplicate_key": duplicate_key,
    }
