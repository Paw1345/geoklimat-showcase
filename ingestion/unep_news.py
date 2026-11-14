"""Official UNEP news list cards only; no child pages or media downloads."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

# The former address /news-and-stories redirects here (reviewed 2026-10-22).
LEGACY_URL = "https://www.unep.org/news-and-stories"
URL = "https://www.unep.org/en/whats-new/news-stories-speeches"
CONFIG = {"strict_endpoint": True, "unep_news": "landing-cards"}
LEGACY_NOTE = (
    "Metadane czterech sekcji kart oficjalnej strony UNEP News, Stories & Speeches, "
    "co 6h. Daty i rodzaje podane na kartach; bez pełnych tekstów, filmów, "
    "obrazów, stron filtrów i pełnego archiwum."
)
NOTE = (
    "Metadane kart listy wiadomości oficjalnej strony UNEP News, Stories & Speeches, "
    "co 6h. Daty i rodzaje podane na kartach; bez pełnych tekstów, filmów, "
    "obrazów, stron filtrów i pełnego archiwum."
)
TITLE = "News, stories & speeches | UNEP - UN Environment Programme"
LIST = ".view-promoted-news.view-display-id-news_page > .view-content"
# Source labels shown on cards and the segment of the article URL they must match.
KINDS = {
    "Story": "story",
    "Speech": "speech",
    "Press release": "press-release",
    "Opinion": "opinion",
    "Technical highlight": "technical-highlight",
    "Video": "video",
    "Statements": "statements",
}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def source_day(raw):
    match = re.fullmatch(r"([0-9]{1,2}) ([A-Za-z]{3}) ((?:19|20)[0-9]{2})", raw)
    if not match or match[2] not in MONTHS:
        raise FetchError("UNEP card date format changed")
    try:
        return datetime(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1])).date()
    except ValueError as exc:
        raise FetchError("UNEP card calendar date invalid") from exc


def parse_news(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("UNEP endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("UNEP page encoding changed") from exc
    titles, lists = soup.select("head > title"), soup.select(LIST)
    if len(titles) != 1 or text(titles[0].get_text()) != TITLE or len(lists) != 1:
        raise FetchError("UNEP page identity/container changed")
    rows = lists[0].select(":scope > .views-row")
    if not 1 <= len(rows) <= 100:
        raise FetchError("UNEP card list missing or budget changed")
    entries, urls = [], set()
    for row in rows:
        cards = row.select(":scope > div.node--type-news")
        if len(cards) != 1:
            raise FetchError("UNEP card content changed")
        card = cards[0]
        tags = card.select(".card__tag")
        times = card.select(".card__meta time")
        links = card.select("h3 > a")
        if len(tags) != 1 or len(times) != 1 or len(links) != 1 or len(card.select("a")) != 1:
            raise FetchError("UNEP card fields changed")
        kind = text(tags[0].get_text())
        if kind not in KINDS:
            raise FetchError("UNEP card kind label changed")
        raw_day = text(times[0].get_text())
        day = source_day(raw_day)
        raw_timestamp = times[0].get("datetime", "")
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}",
            raw_timestamp,
        ):
            raise FetchError("UNEP card timestamp format changed")
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
        except ValueError as exc:
            raise FetchError("UNEP card timestamp invalid") from exc
        # The visible label is the local calendar day of the timestamp's own offset.
        if timestamp.date() != day:
            raise FetchError("UNEP card displayed/timestamp date conflict")
        path = links[0].get("href", "")
        title = text(links[0].get_text())
        if (
            not re.fullmatch(
                r"/en/news-and-stories/" + re.escape(KINDS[kind]) + r"/[a-z0-9-]{1,350}",
                path,
            )
            or not 1 <= len(title) <= 1000
            or path in urls
            or links[0].get("hreflang") != "en"
        ):
            raise FetchError("UNEP card link/identity changed")
        urls.add(path)
        entries.append(
            Entry(
                # Key keeps the pre-2026-10-22 shape (no /en) so stored items stay the same.
                "unep:" + path.removeprefix("/en"),
                title,
                "https://www.unep.org" + path,
                "",
                day.isoformat(),
                "",
                {
                    "record_kind": "news_notice",
                    "listing_scope": "unep_landing_cards",
                    "source_document_type": kind,
                    "source_publication_date_text": raw_day,
                    "source_card_timestamp": raw_timestamp,
                    "source_date_precision": "day",
                    "full_document_downloaded": False,
                    "media_downloaded": False,
                    "full_archive_downloaded": False,
                    "funding_call_confirmed": False,
                },
            )
        )
    return entries
