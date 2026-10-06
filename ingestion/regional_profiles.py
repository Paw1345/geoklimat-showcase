"""Explicit thematic endpoints inspected 2026-09-23; not whole institution home pages."""

import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from ingestion.adapters import Entry, date_value
from ingestion.transport import FetchError

PROFILES = {
    84: ("feed", "https://www.wios.lublin.pl/feed/", {"strict_endpoint": True}),
    99: (
        "web",
        "https://wfosigw.torun.pl/aktualnosci?field_tags_target_id=All",
        {"regional_listing": "fund_torun"},
    ),
    106: (
        "feed",
        "https://bip.wfosigw.rzeszow.pl/aktualnosci?format=feed&type=rss",
        {"strict_endpoint": True},
    ),
    107: ("feed", "https://wfosigw.bialystok.pl/feed/", {"strict_endpoint": True}),
    126: ("web", "https://kujawsko-pomorskie.pl/tag/srodowisko/", {"regional_listing": "kujawsko"}),
    127: (
        "feed",
        "https://www.lubelskie.pl/kategoria/aktualnosci-srodowisko/feed/",
        {"strict_endpoint": True, "regional_feed": "lubelskie"},
    ),
    128: ("web", "https://lubuskie.pl/wiadomosci/kategoria/87", {"regional_listing": "lubuskie"}),
    129: ("web", "https://www.lodzkie.pl/srodowisko", {"regional_listing": "lodzkie"}),
    130: (
        "web",
        "https://www.malopolska.pl/aktualnosci?f%5B0%5D=aktualnosci%3A262",
        {"regional_listing": "malopolskie"},
    ),
    135: ("web", "https://pomorskie.eu/srodowisko/", {"regional_listing": "pomorskie"}),
    133: (
        "feed",
        "https://podkarpackie.pl/index.php/srodowisko/aktualnosci?format=feed&type=rss",
        {"strict_endpoint": True, "regional_feed": "podkarpackie"},
    ),
    140: (
        "web",
        "https://wzp.pl/dla-mieszkancow/ekologia-i-srodowisko/",
        {"regional_listing": "zachodniopomorskie"},
    ),
    139: (
        "web",
        "https://www.umww.pl/kategoria/aktualnosci-korzystania-i-informacji-o-srodowisku",
        {"regional_listing": "wielkopolskie"},
    ),
    137: (
        "web",
        "https://www.swietokrzyskie.pro/category/urzad-marszalkowski/departamenty/departament-srodowiska/",
        {"regional_listing": "swietokrzyskie"},
    ),
    138: (
        "feed",
        "https://warmia.mazury.pl/srodowisko/aktualnosci?format=feed&type=rss",
        {"strict_endpoint": True},
    ),
}
NOTE = (
    "Monitoring publikacji: WIOŚ, WFOŚiGW lub wydzielona sekcja środowiskowa urzędu. "
    "Bieżące okno RSS/listy, nie pełne archiwum. Bez pobierania załączników."
)
SELECTORS = {
    "zachodniopomorskie": (
        "article.p-news-tiles-item",
        ".p-news-tiles-item__title a",
        ":not(*)",
        ".p-news-tiles-item__date",
    ),
    "malopolskie": (
        "main .wiersza-aktualnosci-sp2.views-row",
        ".zajawka > a",
        ".info",
        "time.datetime",
    ),
    "fund_torun": (
        ".wiersza-aktualnosci-sp2.views-row",
        ".zajawka > a",
        ".info",
        "time.datetime",
    ),
    "pomorskie": ("a.article-item", None, ":not(*)", None),
    "wielkopolskie": ("main .news-lista-elem", "a.link-heading", "p.size-17", ".z-bigger-date p"),
    "swietokrzyskie": ("article.clist_box1a", "a.clist_box1b", ":not(*)", "time"),
    "kujawsko": ("article.post", "a.entry-permalink", ".entry-description", None),
    "lubuskie": ("main .tile-content", "a.tile-title", ".tile-description", ".tile-date"),
    "lodzkie": (
        "article.catItemView",
        "h3.catItemTitle a",
        ".catItemIntroText",
        ".catItemDateCreated",
    ),
}
MONTHS = (
    "stycznia lutego marca kwietnia maja czerwca lipca sierpnia "
    "września października listopada grudnia"
).split()


def polish_date(value):
    parsed = date_value(value)
    if parsed:
        return parsed.date().isoformat()
    match = re.search(r"\b(\d{1,2})\s+(\w+)\s+(\d{4})\b", value.casefold())
    try:
        if match:
            return date(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1])).isoformat()
    except ValueError:
        pass
    raise FetchError("Regional listing publication date changed")


def validate_regional_feed(source, feed, response):
    reviewed = {
        "lubelskie": (
            127,
            "Aktualności środowisko",
            100,
            "www.lubelskie.pl",
            ("/aktualnosci/", "/aktualnosci-srodowisko/"),
        ),
        "podkarpackie": (
            133,
            "Portal Samorządu Województwa Podkarpackiego - Aktualności",
            200,
            "podkarpackie.pl",
            ("/index.php/srodowisko/aktualnosci/",),
        ),
    }
    name = source.config.get("regional_feed")
    if name not in reviewed:
        raise FetchError("Unknown regional feed")
    key, title, budget, host, paths = reviewed[name]
    if (source.adapter, source.url, source.config) != PROFILES[key] or response.url != source.url:
        raise FetchError("Regional feed left reviewed section")
    if (
        not feed.feed.get("title", "").startswith(title)
        or not feed.entries
        or len(feed.entries) > budget
    ):
        raise FetchError("Regional environmental feed missing or changed")
    seen = set()
    for row in feed.entries:
        url = row.get("link", "")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != host
            or not parts.path.startswith(paths)
            or url in seen
            or (
                name == "lubelskie"
                and "Aktualności środowisko" not in [tag.get("term") for tag in row.get("tags", [])]
            )
            or not date_value(row.get("published", ""))
        ):
            raise FetchError("Regional feed identity, category or date changed")
        seen.add(url)


def parse_regional(source, soup, response):
    name = source.config.get("regional_listing")
    expected = [
        (adapter, url, config)
        for adapter, url, config in PROFILES.values()
        if config.get("regional_listing") == name
    ]
    if (
        not expected
        or (source.adapter, source.url, source.config) != expected[0]
        or response.url != source.url
    ):
        raise FetchError("Regional listing left reviewed section")
    row_selector, link_selector, summary_selector, date_selector = SELECTORS[name]
    rows = soup.select(row_selector)
    if not rows or len(rows) > 100:
        raise FetchError("Regional listing missing or row budget exceeded")
    entries, seen = [], set()
    for row in rows:
        link = row.select_one(link_selector) if link_selector else row
        if not link or not link.get("href"):
            raise FetchError("Regional listing title link missing")
        url = urljoin(response.url, link["href"])
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != urlsplit(source.url).netloc or url in seen:
            raise FetchError("Regional listing external or duplicate identity")
        if name == "malopolskie":
            category = row.select_one(".kategoria-tresci")
            if (
                not parts.path.startswith("/aktualnosci/srodowisko/")
                or category is None
                or category.get_text(" ", strip=True) != "Środowisko"
            ):
                raise FetchError("Regional listing left environmental category")
        if name == "zachodniopomorskie":
            category = row.select_one(".p-news-tiles-item__category")
            if (
                not parts.path.startswith(
                    (
                        "/aktualnosci/newsy/ekologia-srodowisko/",
                        "/aktualnosci/komunikaty-urzedowe/ekologia-srodowisko/",
                    )
                )
                or category is None
                or category.get_text(" ", strip=True) != "Ekologia i środowisko"
                or urljoin(response.url, category.get("href", "")) != url.rsplit("/", 2)[0] + "/"
            ):
                raise FetchError("Regional listing left environmental category")
        seen.add(url)
        heading_selector = {
            "fund_torun": ".podtytul",
            "malopolskie": ".podtytul",
            "swietokrzyskie": ".clist_title",
            "wielkopolskie": "h3",
            "pomorskie": ".article-title h3",
        }.get(name)
        heading = row.select_one(heading_selector) if heading_selector else link
        title = link.get("title") or (heading.get_text(" ", strip=True) if heading else "")
        summary = row.select_one(summary_selector)
        published = ""
        if date_selector:
            node = row.select_one(date_selector)
            if node is None:
                raise FetchError("Regional listing date missing")
            if name in ("fund_torun", "malopolskie"):
                value = node.get("datetime", "")
                parsed = date_value(value)
                expected_value = value.replace("Z", "+00:00") if name == "malopolskie" else value
                if not parsed or "T" not in value or parsed.isoformat() != expected_value:
                    raise FetchError("Regional listing timestamp missing or invalid")
                published = value
            else:
                published = polish_date(node.get_text(" ", strip=True))
        if not title:
            raise FetchError("Regional listing title missing")
        entries.append(
            Entry(
                url,
                title,
                url,
                summary.get_text(" ", strip=True) if summary else "",
                published,
                metadata={"record_kind": "news", "listing_scope": "current_window"},
            )
        )
    return entries
