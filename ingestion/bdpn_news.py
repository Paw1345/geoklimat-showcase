"""Reviewed official park news cards, first page only."""

import re
from datetime import date

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

LEGACY = "https://www.bdpn.pl/"
URL = "https://bdpn.gov.pl/aktualnosci"
CONFIG = {"strict_endpoint": True, "bdpn_news": "first-page"}
NOTE = (
    "Aktualności Bieszczadzkiego Parku Narodowego z pierwszej strony co 6h. "
    "Tytuły, skróty i daty publikacji; bez pełnych artykułów, zdjęć, załączników, "
    "paginacji i pełnego archiwum. BIP, plany ochrony i konsultacje wymagają "
    "osobnego konektora."
)
MONTHS = (
    "stycznia lutego marca kwietnia maja czerwca lipca sierpnia września "
    "października listopada grudnia"
).split()


def single(root, selector):
    nodes = root.select(selector)
    if len(nodes) != 1:
        raise FetchError("BDPN field missing/ambiguous")
    return nodes[0]


def parse_news(source, response):
    return parse_cards(source, response, URL, CONFIG, "bdpn_initial_news_cards")


def parse_cards(source, response, url, config, listing_scope):
    if (
        source.adapter != "web"
        or source.url != url
        or source.config != config
        or response.status != 200
        or response.url != url
    ):
        raise FetchError("BDPN endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("BDPN encoding changed") from exc
    main = single(soup, "main#main-content")
    if text(single(main, "h1").get_text()) != "Aktualności":
        raise FetchError("BDPN page identity changed")
    root = single(main, ".articles-grid.js-pagination-result-container-news")
    if root.get("data-pagination-url") != "/pl/front-api/news":
        raise FetchError("BDPN listing identity changed")
    cards = root.select(":scope > a.article-tile")
    if not 1 <= len(cards) <= 100 or len(root.select("a")) != len(cards):
        raise FetchError("BDPN listing budget/links changed")
    entries, seen = [], set()
    for card in cards:
        path = card.get("href", "")
        title = text(single(card, "h3.article-tile__title").get_text())
        summary = text(single(card, ".article-tile__excerpt").get_text())
        stamp = single(card, "time.article-tile__publication-date")
        raw = stamp.get("datetime", "")
        if (
            not re.fullmatch(r"/aktualnosci/[a-z0-9-]{1,350}", path)
            or path in seen
            or not 1 <= len(title) <= 1000
            or len(summary) > 5000
            or not re.fullmatch(r"(?:19|20)[0-9]{2}-[0-9]{2}-[0-9]{2}", raw)
        ):
            raise FetchError("BDPN card identity/date/budget changed")
        try:
            day = date.fromisoformat(raw)
        except ValueError as exc:
            raise FetchError("BDPN calendar date invalid") from exc
        label = text(stamp.get_text())
        if label != f"{day.day} {MONTHS[day.month - 1]} {day.year}":
            raise FetchError("BDPN displayed/date attribute conflict")
        seen.add(path)
        item_url = url.removesuffix("/aktualnosci") + path
        entries.append(
            Entry(
                key=item_url,
                title=title,
                url=item_url,
                summary=summary,
                published=raw,
                metadata={
                    "listing_scope": listing_scope,
                    "source_publication_date_text": label,
                    "article_body_stored": False,
                    "legal_status_verified": False,
                    "funding_open_confirmed": False,
                    "archive_complete": False,
                },
            )
        )
    return entries
