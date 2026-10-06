import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup
from pypdf import PdfReader

from ingestion.transport import FetchError


@dataclass
class Entry:
    key: str
    title: str
    url: str
    summary: str = ""
    published: str = ""
    updated: str = ""
    metadata: dict = field(default_factory=dict)

    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def text(value):
    return " ".join(BeautifulSoup(str(value or ""), "html.parser").get_text(" ").split())


def date_value(value):
    if not value:
        return None
    for parse in (
        datetime.fromisoformat,
        parsedate_to_datetime,
        lambda v: datetime.strptime(v, "%d.%m.%Y"),
    ):
        try:
            result = parse(value.strip())
            # Preserve day-only values in metadata; no inferred effective dates.
            return result.replace(tzinfo=UTC) if result.tzinfo is None else result
        except (ValueError, TypeError):
            continue
    return None


def safe_entry(entry, base_url):
    if not entry.url:
        return None
    entry.url = urljoin(base_url, entry.url)
    parts = urlsplit(entry.url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        return None
    entry.title = text(entry.title)[:1000]
    entry.summary = text(entry.summary)[:600]
    if not entry.title or len(entry.url) > 1500:
        return None
    entry.key = hashlib.sha256((entry.key or entry.url).encode()).hexdigest()
    entry.metadata.update(
        {"publication_date_raw": entry.published, "updated_date_raw": entry.updated}
    )
    return entry


def parse(source, response):
    content = response.body
    entries = []
    if "imgw_news" in source.config:
        from ingestion.imgw_news import parse_news

        entries = parse_news(source, response)
    elif source.adapter in {"web", "json", "ogc", "stac"}:
        from ingestion.extended_adapters import parse_extended

        entries = parse_extended(source, response)
    elif source.adapter == "feed":
        feed = feedparser.parse(content)
        if feed.bozo or not feed.version:
            raise FetchError("Malformed RSS/Atom feed")
        if "reviewed_regional_rss" in source.config:
            from ingestion.regional_rss import parse_feed

            return parse_feed(source, feed, response)
        if source.config.get("opole_bip"):
            from ingestion.opole_bip import parse_feed

            return parse_feed(source, feed, response)
        if source.config.get("regional_feed"):
            from ingestion.regional_profiles import validate_regional_feed

            validate_regional_feed(source, feed, response)
        for row in feed.entries:
            entries.append(
                Entry(
                    row.get("id", row.get("link", "")),
                    row.get("title", ""),
                    row.get("link", ""),
                    row.get("summary", ""),
                    row.get("published", ""),
                    row.get("updated", ""),
                    {"authors": [author.get("name", "") for author in row.get("authors", [])]},
                )
            )
    elif source.adapter == "eli":
        from ingestion.eli_catalogue import decode, parse_rows

        _, rows, total, offset = decode(content)
        if offset != 0 or total != len(rows):
            raise FetchError("ELI response is paginated; catalog adapter needs pagination")
        entries = parse_rows(rows)
    elif source.adapter == "pdf":
        if not content.startswith(b"%PDF"):
            raise FetchError("Response is not PDF")
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or len(reader.pages) > 200:
            raise FetchError("Encrypted or over-200-page PDF requires manual review")
        excerpt = " ".join(page.extract_text() or "" for page in list(reader.pages)[:3])
        if not excerpt.strip():
            raise FetchError("PDF has no text layer; OCR is not configured")
        entries.append(
            Entry(
                source.url,
                source.name,
                response.url,
                excerpt,
                metadata={
                    "document_hash": hashlib.sha256(content).hexdigest(),
                    "pages": len(reader.pages),
                    "extracted_pages": min(3, len(reader.pages)),
                },
            )
        )
    else:
        soup = BeautifulSoup(content, "html.parser")
        for node in soup.select("script, style, nav, footer, [role=navigation]"):
            node.decompose()
        if source.adapter == "watch":
            from ingestion.extended_adapters import snapshot_content

            body = snapshot_content(source, soup)
            if body is None:
                raise FetchError("Watched content selector missing")
            body_text = text(str(body))
            entries.append(
                Entry(
                    source.url,
                    source.name,
                    response.url,
                    body_text,
                    metadata={"document_hash": hashlib.sha256(body_text.encode()).hexdigest()},
                )
            )
        elif source.adapter == "html":
            rows = soup.select(source.config["row"])
            if not rows:
                raise FetchError("HTML listing empty or layout changed")
            for row in rows:
                link = row.select_one(source.config["link"])
                title = row.select_one(source.config["title"])
                date = row.select_one(source.config.get("date", "time"))
                summary = row.select_one(source.config.get("summary", ".intro"))
                if link is None or title is None:
                    entries.append(Entry("", "", ""))
                    continue
                url = urljoin(response.url, link.get("href", ""))
                entries.append(
                    Entry(
                        url,
                        title.get_text(" ", strip=True),
                        url,
                        summary.get_text(" ", strip=True) if summary else "",
                        date.get("datetime", date.get_text(strip=True)) if date else "",
                    )
                )
        else:
            raise FetchError("Unsupported adapter")
    if len(entries) > 10000:
        raise FetchError("Item limit exceeded")
    valid, rejected = [], 0
    for entry in entries:
        normalized = safe_entry(entry, response.url)
        if normalized:
            valid.append(normalized)
        else:
            rejected += 1
    if rejected and not valid:
        raise FetchError("No valid entries; adapter needs review")
    return valid, rejected
