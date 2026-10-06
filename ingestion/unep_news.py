"""Official UNEP landing-page cards only; no child pages or media downloads."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

URL = "https://www.unep.org/news-and-stories"
CONFIG = {"strict_endpoint": True, "unep_news": "landing-cards"}
NOTE = (
    "Metadane czterech sekcji kart oficjalnej strony UNEP News, Stories & Speeches, "
    "co 6h. Daty i rodzaje podane na kartach; bez pełnych tekstów, filmów, "
    "obrazów, stron filtrów i pełnego archiwum."
)
GROUPS = {
    "news_featured_item": "news_featured_item_text",
    "secondnewslist_item": "secondnewslist_item_text",
    "news_whitearea_item": "news_whitearea_item_text",
    "news_whitearea_additional_item": "news_whitearea_item_text",
}
KINDS = {
    "Story": "story",
    "Speech": "speech",
    "Press release": "press-release",
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
    titles, roots = soup.select("head > title"), soup.select(".gwm_ts_body.press_room")
    if (
        len(titles) != 1
        or text(titles[0].get_text())
        != "News, Stories & Speeches | UNEP - UN Environment Programme"
        or len(roots) != 1
    ):
        raise FetchError("UNEP page identity/container changed")
    root = roots[0]
    headings = root.select(".news_header > h1")
    if len(headings) != 1 or text(headings[0].get_text()) != "News, Stories & Speeches":
        raise FetchError("UNEP page heading changed")
    entries, urls = [], set()
    for group, content_class in GROUPS.items():
        cards = root.select("." + group)
        if not 1 <= len(cards) <= 100:
            raise FetchError("UNEP card group missing or budget changed")
        for card in cards:
            containers = card.select("." + content_class)
            if len(containers) != 1:
                raise FetchError("UNEP card content changed")
            content = containers[0]
            metas = content.select(":scope > ." + content_class + "_meta")
            links = content.select(":scope > a")
            if len(metas) != 1 or len(links) != 1 or len(card.select("a")) != 1:
                raise FetchError("UNEP card fields changed")
            # Reviewed template emits literal backslash-n whitespace in plain dates.
            label = text(metas[0].get_text(" ", strip=True).replace("\\n", " "))
            parts = label.split(" | ")
            if len(parts) != 2 or parts[1] not in KINDS:
                raise FetchError("UNEP card kind/date label changed")
            raw_day, kind = parts
            day = source_day(raw_day)
            times = metas[0].select("time")
            raw_timestamp = ""
            if len(times) > 1:
                raise FetchError("UNEP card time ambiguous")
            if times:
                raw_timestamp = times[0].get("datetime", "")
                if (
                    not re.fullmatch(
                        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}",
                        raw_timestamp,
                    )
                    or text(times[0].get_text()) != raw_day
                ):
                    raise FetchError("UNEP card timestamp format changed")
                try:
                    timestamp = datetime.fromisoformat(raw_timestamp)
                except ValueError as exc:
                    raise FetchError("UNEP card timestamp invalid") from exc
                if timestamp.date() != day:
                    raise FetchError("UNEP card displayed/timestamp date conflict")
            path = links[0].get("href", "")
            title = text(links[0].get_text())
            if (
                not re.fullmatch(
                    r"/news-and-stories/" + re.escape(KINDS[kind]) + r"/[a-z0-9-]{1,350}",
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
                    "unep:" + path,
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
    if len(entries) > 200:
        raise FetchError("UNEP combined card budget changed")
    return entries
