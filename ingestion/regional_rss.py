"""Reviewed regional institution RSS windows, not full source products."""

import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlsplit

from ingestion.adapters import Entry, safe_entry
from ingestion.transport import FetchError

PROFILES = {
    82: {
        "home": "https://www.wroclaw.wios.gov.pl/",
        "title": "Wojewódzki Inspektorat Ochrony Środowiska we Wrocławiu",
    },
    94: {
        "home": "https://kielce.wios.gov.pl",
        "legacy_home": "https://www.kielce.wios.gov.pl/",
        "title": "Wojewódzki Inspektorat Ochrony Środowiska w Kielcach",
    },
    96: {
        "home": "https://poznan.wios.gov.pl",
        "legacy_home": "https://www.poznan.wios.gov.pl/",
        "title": "Wojewódzki Inspektorat Ochrony Środowiska w Poznaniu",
        "dated_path": True,
        "categories": {"Aktualności", "Komunikaty", "Bez kategorii"},
    },
    108: {
        "home": "https://wfos.gdansk.pl",
        "title": "Wojewódzki Fundusz Ochrony Środowiska i Gospodarki Wodnej w Gdańsku",
    },
    109: {
        "home": "https://wfosigw.katowice.pl",
        "legacy_home": "https://www.wfosigw.katowice.pl",
        "title": "WFOŚiGW w Katowicach",
        "reviewed_rss_tracking": True,
    },
    111: {
        "home": "https://wfosigw.olsztyn.pl",
        "title": "WFOSiGW",
        "dated_path": True,
    },
}
NOTE = (
    "Metadane bieżącego okna oficjalnego RSS aktualności: tytuły, daty i linki. "
    "Bez autorów, opisów, załączników i archiwum; komunikat o naborze nie jest "
    "zweryfikowanym naborem ani dowodem jego aktualności."
)


def profile(key):
    if type(key) is not int or key not in PROFILES:
        raise FetchError("Unknown reviewed regional RSS profile")
    return {
        **PROFILES[key],
        "slug": f"registry-{key:03d}",
        "url": PROFILES[key]["home"].rstrip("/") + "/feed/",
        "config": {"strict_endpoint": True, "reviewed_regional_rss": key},
    }


def validate_date(raw):
    try:
        value = parsedate_to_datetime(raw)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Missing timezone")
    except (TypeError, ValueError, AttributeError) as exc:
        raise FetchError("Regional RSS source date changed") from exc


def parse_feed(source, feed, response):
    p = profile(source.config.get("reviewed_regional_rss"))
    if (
        (source.adapter, source.url, source.config) != ("feed", p["url"], p["config"])
        or response.status != 200
        or response.url != p["url"]
        or feed.feed.get("title") != p["title"]
        or feed.feed.get("link") != p["home"]
        or [link.get("href") for link in feed.feed.get("links", []) if link.get("rel") == "self"]
        != [p["url"]]
        or not 1 <= len(feed.entries) <= 50
    ):
        raise FetchError("Regional RSS identity or window budget changed")
    host = urlsplit(p["home"]).netloc
    entries, urls, identities = [], set(), set()
    for row in feed.entries:
        url, identity = row.get("link", ""), row.get("id", "")
        parts = urlsplit(url)
        categories = {tag.get("term") for tag in row.get("tags", [])}
        path_pattern = (
            r"/20\d{2}/(?:0[1-9]|1[0-2])/(?:0[1-9]|[12][0-9]|3[01])/[a-z0-9%-]+/"
            if p.get("dated_path")
            else r"/[a-z0-9%-]+/"
        )
        # Only Katowice's reviewed RSS campaign tuple can be removed; do not
        # weaken query rejection on other profiles or drop substantive params.
        query_valid = not parts.query
        if p.get("reviewed_rss_tracking") and parts.query:
            try:
                pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
            except ValueError as exc:
                raise FetchError("Regional RSS tracking query changed") from exc
            query_valid = len(pairs) == 3 and dict(pairs) == {
                "utm_source": "rss",
                "utm_medium": "rss",
                "utm_campaign": parts.path.strip("/"),
            }
        category_valid = (
            bool(categories) and categories <= p["categories"]
            if "categories" in p
            else "Aktualności" in categories
        )
        if (
            parts.scheme != "https"
            or parts.netloc != host
            or not query_valid
            or parts.fragment
            or not re.fullmatch(path_pattern, parts.path)
            or not re.fullmatch(
                re.escape("https://" + host + "/?p=") + r"[1-9][0-9]{0,11}", identity
            )
            or len(url) > 1500
            or url in urls
            or identity in identities
            or not category_valid
        ):
            raise FetchError("Regional RSS record identity or category changed")
        published = row.get("published", "")
        updated = row.get("updated", "") if "updated" in row else ""
        validate_date(published)
        if updated:
            validate_date(updated)
        if p.get("reviewed_rss_tracking"):
            url = p["home"] + parts.path
        if url in urls:
            raise FetchError("Regional RSS canonical article duplicated")
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
                    "source_content_type": "institution_news",
                    "full_document_downloaded": False,
                    "funding_call_verified": False,
                },
            ),
            response.url,
        )
        if not entry:
            raise FetchError("Regional RSS title or record invalid")
        if "categories" in p:
            # Retain source categorization; uncategorized is not inspection evidence.
            entry.metadata["source_categories"] = sorted(categories)
        urls.add(url)
        identities.add(identity)
        entries.append(entry)
    return entries, 0
