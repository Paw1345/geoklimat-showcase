"""Reviewed first news page; source publication days, no body or archive crawl."""

import re
from datetime import datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

LEGACY_HOME = "https://www.wios.bydgoszcz.pl/"
URL = "https://wios.bydgoszcz.pl/aktualnosci/"
CONFIG = {"strict_endpoint": True, "wios_bydgoszcz": True}
NOTE = (
    "Metadane pierwszej strony Aktualności WIOŚ Bydgoszcz: tytuły, źródłowe "
    "daty dzienne i linki. Bez opisów, kontaktów, autorów, załączników, pełnego "
    "archiwum i gwarancji pełnego pokrycia kontroli/awarii."
)


def parse_listing(source, response):
    if (
        (source.adapter, source.url, source.config) != ("web", URL, CONFIG)
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("WIOS Bydgoszcz endpoint identity changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("WIOS Bydgoszcz UTF-8 changed") from exc
    titles, mains = soup.select("title"), soup.select("main")
    if (
        len(titles) != 1
        or text(titles[0]) != "Wojewódzki Inspektorat Ochrony Środowiska w Bydgoszczy | Aktualności"
        or len(mains) != 1
    ):
        raise FetchError("WIOS Bydgoszcz page identity changed")
    headings = mains[0].select("h1")
    if len(headings) != 1 or text(headings[0]) != "Aktualności":
        raise FetchError("WIOS Bydgoszcz section changed")
    rows = mains[0].select(".post-list-item")
    if not 1 <= len(rows) <= 50:
        raise FetchError("WIOS Bydgoszcz listing missing or budget changed")
    entries, ids, urls, previous_day = [], set(), set(), None
    for row in rows:
        identity = re.fullmatch(r"post-([1-9][0-9]{0,11})", row.get("id", ""))
        if not identity:
            raise FetchError("WIOS Bydgoszcz source record ID changed")
        record_id = identity[1]
        if not {"type-post", "status-publish", "post-" + record_id} <= set(row.get("class", [])):
            raise FetchError("WIOS Bydgoszcz publication status changed")
        headings = row.select("h2")
        links = row.select("h2 a[href]")
        more = row.select("a.post-item-link[href]")
        dates = row.select(".post-date")
        if len(headings) != 1 or len(links) != 1 or len(more) != 1 or len(dates) != 1:
            raise FetchError("WIOS Bydgoszcz record fields missing or ambiguous")
        title, url, raw_day = text(links[0]), links[0]["href"], text(dates[0])
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "wios.bydgoszcz.pl"
            or parts.query
            or parts.fragment
            or not re.fullmatch(r"/aktualnosci/[a-z0-9%-]+/", parts.path)
            or more[0]["href"] != url
            or not title
            or len(title) > 1000
            or len(url) > 1500
            or record_id in ids
            or url in urls
            or not re.fullmatch(r"\d{2}\.\d{2}\.20\d{2}", raw_day)
        ):
            raise FetchError("WIOS Bydgoszcz record identity or publication day changed")
        try:
            day = datetime.strptime(raw_day, "%d.%m.%Y").date().isoformat()
        except ValueError as exc:
            raise FetchError("WIOS Bydgoszcz publication day invalid") from exc
        if previous_day is not None and day > previous_day:
            raise FetchError("WIOS Bydgoszcz date ordering changed")
        entries.append(
            Entry(
                "wios-bydgoszcz:post:" + record_id,
                title,
                url,
                "",
                day,
                "",
                {
                    "record_kind": "news_notice",
                    "listing_scope": "first_listing_page",
                    "source_record_id": record_id,
                    "source_publication_date_text": raw_day,
                    "publication_date_precision": "day",
                    "full_document_downloaded": False,
                },
            )
        )
        ids.add(record_id)
        urls.add(url)
        previous_day = day
    return entries
