"""Allowed official HTML press listing, not the robots-disallowed RSS endpoints."""

import re
from datetime import date

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

URL = "https://www.eea.europa.eu/en/newsroom/news"
SLUG = "registry-175-press-list"
CONFIG = {"eea_press": "v1"}
NOTE = (
    "Komunikaty EEA: pierwsza strona oficjalnej listy HTML, metadane i daty dzienne; "
    "bez pełnego archiwum, raportów i plików."
)
MONTHS = {
    name: i
    for i, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"], 1
    )
}


def one(node, selector):
    found = node.select(selector)
    if len(found) != 1:
        raise FetchError("EEA listing required field missing or ambiguous")
    return found[0]


def parse_press(source, response):
    if (source.adapter, source.url, source.config) != ("web", URL, CONFIG) or (
        response.status != 200 or response.url != URL
    ):
        raise FetchError("EEA listing profile/endpoint mismatch")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeError as exc:
        raise FetchError("EEA listing is not UTF8") from exc
    if (
        text(one(soup, "head > title").get_text())
        != "Press releases | Newsroom | European Environment Agency (EEA)"
    ):
        raise FetchError("EEA listing title changed")
    main = one(soup, "main")
    if text(one(main, "h1").get_text()) != "Press releases":
        raise FetchError("EEA listing heading changed")
    root = one(main, ".block.listing.summary .items.item-items")
    cards = root.select(".listing-item")
    if not 1 <= len(cards) <= 100 or len(root.select("h3.listing-header")) != len(cards):
        raise FetchError("EEA listing card budget/schema changed")
    entries, seen, previous = [], set(), None
    for card in cards:
        link = one(card, "a[href]")
        title = text(one(link, "h3.listing-header").get_text())
        summary = text(one(card, "p.listing-description").get_text())
        raw = text(one(card, "p.listing-date").get_text())
        path = link.get("href", "")
        match = re.fullmatch(r"([0-9]{2}) ([A-Za-z]{3,4}) ((?:19|20)[0-9]{2})", raw)
        if (
            not match
            or match[2] not in MONTHS
            or (
                not re.fullmatch(r"/en/newsroom/news/[a-z0-9-]{1,350}", path)
                or path in seen
                or not 1 <= len(title) <= 1000
                or len(summary) > 5000
            )
        ):
            raise FetchError("EEA listing card identity/date changed")
        try:
            day = date(int(match[3]), MONTHS[match[2]], int(match[1]))
        except ValueError as exc:
            raise FetchError("EEA listing calendar date invalid") from exc
        if previous is not None and day > previous:
            raise FetchError("EEA listing date order changed")
        previous = day
        seen.add(path)
        entries.append(
            Entry(
                key=path,
                title=title,
                url="https://www.eea.europa.eu" + path,
                summary=summary,
                published=day.isoformat(),
                metadata={
                    "kind": "news_notice",
                    "listing_scope": "eea_initial_press_cards",
                    "source_publication_date_text": raw,
                    "publication_precision": "day",
                    "article_body_stored": False,
                    "archive_complete": False,
                    "legal_status_verified": False,
                    "funding_open_confirmed": False,
                },
            )
        )
    return entries
