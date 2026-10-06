"""Initial official IPCC report cards, including announced reports; no files."""

import re

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

URL = "https://www.ipcc.ch/reports/"
CONFIG = {"strict_endpoint": True, "ipcc_reports": "initial-cards"}
NOTE = (
    "Metadane początkowych kart oficjalnego katalogu IPCC, co 24h. "
    "Katalog obejmuje także zapowiedziane raporty. Miesiąc/rok nie jest "
    "dzienną datą publikacji ani potwierdzeniem dostępności raportu. "
    "Bez PDF, stron szczegółowych i pełnego archiwum."
)
MONTHS = (
    "January February March April May June July August September October November December"
).split()
KINDS = {
    "Methodology Report",
    "Special Report",
    "Synthesis Report",
    "Report",
    "Working Group Report",
}


def parse_reports(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("IPCC catalogue endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("IPCC catalogue encoding changed") from exc
    titles, headings = soup.select("head > title"), soup.select("h1")
    roots = soup.select("body#page-reports section#reports-post_grid-1")
    if (
        len(titles) != 1
        or text(titles[0].get_text()) != "Reports — IPCC"
        or len(headings) != 1
        or text(headings[0].get_text()) != "Reports"
        or len(roots) != 1
    ):
        raise FetchError("IPCC catalogue page/container changed")
    grids = roots[0].select("#post-grid-1 > .section-posts.post-grid-posts")
    if len(grids) != 1:
        raise FetchError("IPCC initial catalogue grid changed")
    cards = grids[0].select(":scope > .post.post-preview")
    if not 1 <= len(cards) <= 100:
        raise FetchError("IPCC catalogue card budget changed")
    sizers = grids[0].select(":scope > div.grid-sizer")
    if (
        len(sizers) != 1
        or sizers[0].get_text(strip=True)
        or sizers[0].find()
        or len(cards) + 1 != len(grids[0].find_all(recursive=False))
    ):
        raise FetchError("IPCC catalogue unknown grid child")
    entries, urls = [], set()
    for card in cards:
        containers = card.select(".preview-text")
        if len(containers) != 1:
            raise FetchError("IPCC catalogue card container changed")
        container = containers[0]
        labels, links, periods = (
            container.select(":scope > h5"),
            container.select(":scope > h4 > a"),
            container.select(":scope > h6"),
        )
        buttons = card.select(".preview-btns > a")
        if len(labels) != 1 or len(links) != 1 or len(periods) != 1 or len(buttons) != 1:
            raise FetchError("IPCC catalogue card fields changed")
        kind, title = text(labels[0].get_text()), text(links[0].get_text())
        url, raw_period = links[0].get("href", ""), text(periods[0].get_text())
        identity = re.fullmatch(r"https://www\.ipcc\.ch/report/([a-z0-9-]{1,300})/", url)
        period = re.fullmatch(r"([A-Za-z]+) ((?:19|20)[0-9]{2})", raw_period)
        if (
            kind not in KINDS
            or not 1 <= len(title) <= 1000
            or not identity
            or url in urls
            or buttons[0].get("href") != url
            or text(buttons[0].get_text()) != "Explore"
            or len(card.select("a")) != 2
            or not period
            or period[1] not in MONTHS
        ):
            raise FetchError("IPCC catalogue identity/period changed")
        urls.add(url)
        entries.append(
            Entry(
                "ipcc-report:" + identity[1],
                title,
                url,
                "",
                "",
                "",
                {
                    "record_kind": "report_catalogue_metadata",
                    "listing_scope": "ipcc_initial_report_cards",
                    "source_report_type": kind,
                    "source_report_period_text": raw_period,
                    "source_report_month": f"{period[2]}-{MONTHS.index(period[1]) + 1:02d}",
                    "source_report_period_precision": "month",
                    "publication_status_verified": False,
                    "full_document_downloaded": False,
                    "full_archive_downloaded": False,
                },
            )
        )
    return entries
