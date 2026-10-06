"""Reviewed regional fund news; metadata only, never verified open calls."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

PROFILES = {
    101: {
        "host": "wfosigw.zgora.pl",
        "legacy": "https://www.wfosigw.zgora.pl",
        "title": "» Aktualności",
        "container": "main.site-main",
        "heading": "header.header h2.title",
        "section": "Aktualności",
        "rows": ".aktualnosci-js-items > article.type-aktualnosc",
        "budget": 1000,
        "scope": "latest_20_inline_archive_records",
    },
    102: {
        "host": "www.wfosigw.lodz.pl",
        "legacy": "https://www.wfosigw.lodz.pl",
        "title": "Aktualności - Wojewódzki Fundusz Ochrony Środowiska i Gospodarki Wodnej w Łodzi",
        "container": ".main-single-content",
        "heading": "h1.last-head",
        "section": "Aktualności/komunikaty",
        "rows": ".main-archive-page .news-box",
        "budget": 50,
        "scope": "first_listing_page",
    },
}
MONTHS = (
    "stycznia lutego marca kwietnia maja czerwca lipca sierpnia "
    "września października listopada grudnia"
).split()


def profile(key):
    p = PROFILES[key]
    return {
        **p,
        "slug": f"registry-{key:03d}",
        "url": f"https://{p['host']}/aktualnosci/",
        "config": {"strict_endpoint": True, "wfos_news": key},
        "note": (
            "Metadane aktualności WFOŚiGW: tytuł, źródłowa data publikacji i link; "
            + (
                "20 najnowszych wpisów z archiwum osadzonego w stronie. "
                if key == 101
                else "Pierwsza strona listy. "
            )
            + "Bez opisów, autorów, kontaktów, załączników, otwierania artykułów "
            "i pełnego archiwum. Komunikat nie potwierdza aktualnie otwartego naboru."
        ),
    }


def one(node, selector):
    matches = node.select(selector)
    if len(matches) != 1:
        raise FetchError("WFOS news fields missing or ambiguous")
    return matches[0]


def parse_listing(source, response):
    key = source.config.get("wfos_news")
    if type(key) is not int or key not in PROFILES:
        raise FetchError("WFOS news unknown profile")
    p = profile(key)
    if (
        (source.adapter, source.url, source.config) != ("web", p["url"], p["config"])
        or response.status != 200
        or response.url != p["url"]
    ):
        raise FetchError("WFOS news endpoint identity changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("WFOS news UTF-8 changed") from exc
    if text(one(soup, "title")) != p["title"]:
        raise FetchError("WFOS news page identity changed")
    main = one(soup, p["container"])
    headings = [h for h in main.select(p["heading"]) if text(h)]
    if len(headings) != 1 or text(headings[0]) != p["section"]:
        raise FetchError("WFOS news section changed")
    if key == 101:
        one(main, ".aktualnosci-js-items")
    else:
        one(main, ".main-archive-page")
    rows = main.select(p["rows"])
    if not 1 <= len(rows) <= p["budget"]:
        raise FetchError("WFOS news listing missing or budget exceeded")
    entries, ids, urls, previous = [], set(), set(), None
    for row in rows:
        if key == 101:
            classes = set(row.get("class", []))
            identity = [c for c in classes if re.fullmatch(r"post-[1-9][0-9]{0,11}", c)]
            if len(identity) != 1 or not {"aktualnosc", "status-publish"} <= classes:
                raise FetchError("WFOS news publication identity changed")
            record_id = identity[0][5:]
            title = text(one(row, "h3.title"))
            link = one(row, ".permalink a.btn-read-more[href]")
            stamp = one(row, "time.date[datetime]")
            raw_day, published = text(stamp), stamp["datetime"]
            if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+(?:01|02):00", published):
                raise FetchError("WFOS news timestamp changed")
            try:
                instant = datetime.fromisoformat(published)
                d, month, y = raw_day.split()
                day = datetime(int(y), MONTHS.index(month) + 1, int(d)).date()
                if instant.date() != day:
                    raise ValueError("date mismatch")
            except ValueError as exc:
                raise FetchError("WFOS news publication date invalid") from exc
            category = row.get("data-aktualnosc-cats", "").split()
            if len(category) != len(set(category)) or not set(category) <= {
                "komunikaty",
                "programy",
                "spotkania-doradcze",
                "wydarzenia",
            }:
                raise FetchError("WFOS news category changed")
            order = instant.timestamp()
            path_pattern = r"/aktualnosci/[a-z0-9%-]+/"
        else:
            heading = one(row, ".news-title[role=heading]")
            link = one(heading, "a[href]")
            title = text(link)
            if " ".join(heading.get("aria-label", "").split()) != title:
                raise FetchError("WFOS news heading identity changed")
            if one(row, ".news-content-link a[href]")["href"] != link["href"]:
                raise FetchError("WFOS news article links disagree")
            raw_day = text(one(row, ".news-content-date"))
            if not re.fullmatch(r"\d{2}/\d{2}/\d{2}", raw_day):
                raise FetchError("WFOS news publication day changed")
            try:
                d, m, y = map(int, raw_day.split("/"))
                day = datetime(2000 + y, m, d).date()
            except ValueError as exc:
                raise FetchError("WFOS news publication day invalid") from exc
            published, order = day.isoformat(), day.toordinal()
            record_id = link["href"]
            category = []
            path_pattern = r"/(?:ogolne|konkursy-nagroda)/[a-z0-9%-]+/"
        url = link["href"]
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != p["host"]
            or parts.query
            or parts.fragment
            or not re.fullmatch(path_pattern, parts.path)
            or not title
            or len(title) > 1000
            or len(url) > 1500
            or record_id in ids
            or url in urls
            or (previous is not None and order > previous)
        ):
            raise FetchError("WFOS news article identity or ordering changed")
        # Validate the whole bounded inline archive before returning its latest 20.
        if key != 101 or len(entries) < 20:
            entries.append(
                Entry(
                    f"wfos-{key}:" + record_id,
                    title,
                    url,
                    "",
                    published,
                    "",
                    {
                        "record_kind": "news_notice",
                        "listing_scope": p["scope"],
                        "source_record_id": record_id,
                        "source_publication_date_text": raw_day,
                        "source_categories": category,
                        "funding_call_verified": False,
                        "full_document_downloaded": False,
                    },
                )
            )
        ids.add(record_id)
        urls.add(url)
        previous = order
    return entries
