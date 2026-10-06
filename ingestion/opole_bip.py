"""Two reviewed thematic BIP feeds: metadata only, not full regional coverage."""

import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

HOST = "bip.opolskie.pl"
PROFILES = {
    "notices": (
        "ogloszenia-z-zakresu-ochrony-srodowiska",
        "Ogłoszenia z zakresu ochrony środowiska – BIP Opolskie.pl",
    ),
    "register": ("informacje-o-srodowisku", "Informacje o środowisku – BIP Opolskie.pl"),
}
NOTE = (
    "BIP Opolskie: metadane bieżącego okna RSS typu treści; tytuły, "
    "źródłowe daty i odnośniki. Bez pełnych dokumentów, autorów, kontaktów, "
    "załączników, archiwum i dowodu pełnego pokrycia programów/uchwał."
)


def profile(code):
    if code not in PROFILES:
        raise FetchError("Unknown Opole BIP feed")
    path, title = PROFILES[code]
    return {
        "slug": "registry-132-bip-" + code,
        "url": f"https://{HOST}/typy-tresci/{path}/feed/",
        "title": title,
        "config": {"strict_endpoint": True, "opole_bip": code},
    }


def validate_date(raw):
    try:
        date = parsedate_to_datetime(raw)
        if date.tzinfo is None or date.utcoffset() is None:
            raise ValueError("timezone missing")
    except (ValueError, TypeError, AttributeError) as exc:
        raise FetchError("Opole BIP source date changed") from exc


def parse_feed(source, feed, response):
    code = source.config.get("opole_bip")
    p = profile(code)
    if (
        (source.adapter, source.url, source.config) != ("feed", p["url"], p["config"])
        or response.url != p["url"]
        or response.status != 200
        or feed.feed.get("title") != p["title"]
        or feed.feed.get("link") != "https://" + HOST
        or [link.get("href") for link in feed.feed.get("links", []) if link.get("rel") == "self"]
        != [p["url"]]
        or not 1 <= len(feed.entries) <= 50
    ):
        raise FetchError("Opole BIP feed identity or budget changed")
    entries, seen_urls, seen_ids = [], set(), set()
    for row in feed.entries:
        url, identity = row.get("link", ""), row.get("id", "")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != HOST
            or parts.query
            or parts.fragment
            or not re.fullmatch(r"/20\d{2}/(?:0[1-9]|1[0-2])/[a-z0-9%-]+/", parts.path)
            or not re.fullmatch(r"https://bip\.opolskie\.pl/\?p=[1-9][0-9]{0,11}", identity)
            or url in seen_urls
            or identity in seen_ids
            or len(url) > 1500
        ):
            raise FetchError("Opole BIP record identity changed")
        published = row.get("published", "")
        # FeedParserDict.get("updated") aliases published when updated is absent.
        # That compatibility fallback is not evidence of a source modification date.
        updated = row.get("updated", "") if "updated" in row else ""
        validate_date(published)
        if updated:
            validate_date(updated)
        entry = safe_entry(
            Entry(
                identity,
                row.get("title", ""),
                url,
                "",
                published,
                updated,
                {
                    "record_kind": "news_notice",
                    "listing_scope": "current_rss_window",
                    "source_content_type": code,
                    "full_document_downloaded": False,
                },
            ),
            response.url,
        )
        if not entry:
            raise FetchError("Opole BIP title or record invalid")
        seen_urls.add(url)
        seen_ids.add(identity)
        entries.append(entry)
    return entries, 0
