"""Bounded metadata catalogue on the explicitly legacy official GUS portal."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

URL = "https://stat.gov.pl/obszary-tematyczne/srodowisko-energia/"
SUCCESSOR = "https://new.stat.gov.pl/"
CONFIG = {"strict_endpoint": True, "gus_publications": "environment-energy"}
NOTE = (
    "Metadane obu tabel Środowisko/Energia dotychczasowego portalu GUS, co 6h. "
    "Bez stron archiwum, PDF i danych liczbowych. Data z kolumny Data, nie okres "
    "z tytułu. Przejście na nowy portal pozostaje do weryfikacji."
)
CATEGORIES = {"Środowisko": "srodowisko", "Energia": "energia"}
RELATED_ENVIRONMENT_PATHS = {
    "obszary-tematyczne/gospodarka-spoleczna-wolontariat/gospodarka-spoleczna-trzeci-sektor",
    "obszary-tematyczne/inne-opracowania/inne-opracowania-zbiorcze",
    "statystyki-eksperymentalne/bezpieczenstwo-publiczne-organizacja-panstwa",
    "infografiki-widzety/infografiki",
}
HEADERS = ["Lp.", "Rodzaj", "Tytuł dokumentu", "Data", "Archiwum"]
PAGE_TITLE = "Główny Urząd Statystyczny / Obszary tematyczne / Środowisko. Energia"
BANNER = "To jest stara wersja portalu. Kliknij, aby przejść do nowej wersji"


def parse_publications(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.url != URL
        or response.status != 200
    ):
        raise FetchError("GUS catalogue endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("GUS catalogue encoding changed") from exc
    titles = soup.select("head > title")
    roots = soup.select("#content-href")
    banners = soup.select(f'a[href="{SUCCESSOR}"]')
    if (
        len(titles) != 1
        or text(titles[0].get_text()) != PAGE_TITLE
        or len(roots) != 1
        or len(banners) != 1
        or text(banners[0].get_text()) != BANNER
    ):
        raise FetchError("GUS legacy page identity changed")
    root = roots[0]
    headings = root.select(".header-block h2.title")
    tables = root.select("table")
    if (
        len(headings) != 1
        or text(headings[0].get_text()) != "Środowisko. Energia"
        or len(tables) != 2
    ):
        raise FetchError("GUS catalogue containers changed")
    entries, categories, keys, urls = [], set(), set(), set()
    for table in tables:
        heading = table.find_previous("h2")
        category = text(heading.get_text()) if heading else ""
        if (
            category not in CATEGORIES
            or category in categories
            or table.get("title") != category + " tabela"
            or [text(th.get_text()) for th in table.select("thead th")] != HEADERS
        ):
            raise FetchError("GUS catalogue category/header changed")
        categories.add(category)
        slug = CATEGORIES[category]
        rows = table.select("tbody > tr")
        if not 1 <= len(rows) <= 200:
            raise FetchError("GUS catalogue row budget changed")
        previous_day = None
        for ordinal, row in enumerate(rows, 1):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 5 or text(cells[0].get_text()) != str(ordinal):
                raise FetchError("GUS catalogue row shape changed")
            kind = text(cells[1].get_text())
            links = cells[2].find_all("a")
            archives = cells[4].find_all("a")
            if kind not in {"", "Publikacja", "Informacja sygnalna"} or len(links) != 1:
                raise FetchError("GUS catalogue document fields changed")
            link = links[0]
            url = link.get("href", "")
            title = text(link.get_text())
            identity = re.fullmatch(
                r"https://stat\.gov\.pl/([a-z0-9/-]+)/"
                r"[a-z0-9%-]+,([1-9][0-9]{0,8}),([1-9][0-9]{0,8})\.html",
                url,
            )
            allowed_paths = {"obszary-tematyczne/srodowisko-energia/" + slug}
            if category == "Środowisko":
                allowed_paths |= RELATED_ENVIRONMENT_PATHS
            if (
                not identity
                or identity[1] not in allowed_paths
                or not 1 <= len(title) <= 1000
                or text(link.get("title", "")) != title
                or len(archives) > 1
                or (archives and archives[0].get("href") != url + "#archive")
            ):
                raise FetchError("GUS catalogue document identity changed")
            key = f"gus:{identity[1]}:{identity[2]}:{identity[3]}"
            raw_day = text(cells[3].get_text())
            if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", raw_day):
                raise FetchError("GUS catalogue date format changed")
            try:
                day = datetime.strptime(raw_day, "%d.%m.%Y").date().isoformat()
            except ValueError as exc:
                raise FetchError("GUS catalogue invalid calendar date") from exc
            if key in keys or url in urls or (previous_day and day > previous_day):
                raise FetchError("GUS catalogue duplicate/order changed")
            keys.add(key)
            urls.add(url)
            previous_day = day
            entries.append(
                Entry(
                    key,
                    title,
                    url,
                    "",
                    day,
                    "",
                    {
                        "record_kind": "publication_catalogue",
                        "listing_scope": "gus_environment_energy_topic_tables",
                        "source_category": category,
                        "source_document_type": kind,
                        "source_document_namespace": identity[1],
                        "source_series_id": identity[2],
                        "source_edition_id": identity[3],
                        "source_publication_date_text": raw_day,
                        "archive_available": bool(archives),
                        "source_portal_version": "legacy",
                        "successor_portal_url": SUCCESSOR,
                        "full_document_downloaded": False,
                        "numeric_statistics_downloaded": False,
                    },
                )
            )
    return entries
