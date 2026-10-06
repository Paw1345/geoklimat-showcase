"""Two fixed WIOS Lodz listing windows. Read onclick as data, never execute JS."""

import re
from datetime import date

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

HOME = "https://www.wios.lodz.pl/"
PROFILES = {
    "news": ("Aktualnosci", 46, "Aktualności", "registry-086"),
    "notices": ("Komunikaty", 47, "Komunikaty", "registry-086-notices"),
}
MONTHS = (
    "styczeń",
    "luty",
    "marzec",
    "kwiecień",
    "maj",
    "czerwiec",
    "lipiec",
    "sierpień",
    "wrzesień",
    "październik",
    "listopad",
    "grudzień",
)
NOTE = (
    "Metadane pierwszej strony oficjalnej listy WIOŚ Łódź: tytuły, daty dzienne "
    "i odnośniki. Bez opisów, autorów, kontaktów, załączników, pełnego archiwum "
    "i potwierdzenia pełnego pokrycia kontroli/awarii."
)


def profile(code):
    if code not in PROFILES:
        raise FetchError("Unknown WIOS Lodz listing")
    path, category, title, slug = PROFILES[code]
    return {
        "slug": slug,
        "url": HOME + f"{path},{category}",
        "category": category,
        "title": title,
        "config": {"strict_endpoint": True, "wios_lodz": code},
    }


def publication_day(value):
    match = re.fullmatch(r"([1-9]|[12][0-9]|3[01]) (\w+) (20[0-9]{2})", value.casefold())
    try:
        if match:
            return date(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1])).isoformat()
    except ValueError:
        pass
    raise FetchError("WIOS Lodz source publication day changed")


def parse_listing(source, soup, response):
    p = profile(source.config.get("wios_lodz"))
    headings, titles, containers = (
        soup.select("#text_title h2"),
        soup.select("title"),
        soup.select("#text_content"),
    )
    if (
        (source.adapter, source.url, source.config) != ("web", p["url"], p["config"])
        or response.status != 200
        or response.url != p["url"]
        or len(headings) != 1
        or text(headings[0]) != p["title"]
        or len(titles) != 1
        or text(titles[0]) != p["title"] + " - WIOŚ w Łodzi"
        or len(containers) != 1
    ):
        raise FetchError("WIOS Lodz listing identity changed")
    rows = containers[0].select(":scope > .news_spacer")
    if not 1 <= len(rows) <= 50 or len(containers[0].select(".news_spacer")) != len(rows):
        raise FetchError("WIOS Lodz listing missing or budget changed")
    entries, seen_ids, seen_urls, previous_day = [], set(), set(), None
    for row in rows:
        columns = row.select(":scope > .m_news_right")
        if len(columns) != 1:
            raise FetchError("WIOS Lodz record container changed")
        column = columns[0]
        headings = column.select("h3")
        dates = column.select(":scope > .col_gray")
        buttons = column.select("input.more_m")
        if len(headings) != 1 or len(dates) != 1 or len(buttons) != 1:
            raise FetchError("WIOS Lodz record fields missing or ambiguous")
        # Exactly the advertised simple assignment; no evaluation, navigation or child GET.
        match = re.fullmatch(
            r"location\.href='([A-Za-z0-9_-]+)," + str(p["category"]) + r",([1-9][0-9]{0,11})';",
            buttons[0].get("onclick", ""),
        )
        if not match:
            raise FetchError("WIOS Lodz button destination changed")
        identity = match[2]
        url = HOME + f"{match[1]},{p['category']},{identity}"
        title, raw_day = text(headings[0]), text(dates[0])
        day = publication_day(raw_day)
        if (
            not title
            or len(title) > 1000
            or len(url) > 1500
            or identity in seen_ids
            or url in seen_urls
            or (previous_day is not None and day > previous_day)
        ):
            raise FetchError("WIOS Lodz record identity or date ordering changed")
        entries.append(
            Entry(
                f"wios-lodz:{p['category']}:{identity}",
                title,
                url,
                "",
                day,
                "",
                {
                    "record_kind": "news_notice",
                    "listing_scope": "first_listing_page",
                    "source_content_type": source.config["wios_lodz"],
                    "source_record_id": identity,
                    "source_category_id": p["category"],
                    "source_publication_date_text": raw_day,
                    "publication_date_precision": "day",
                    "full_document_downloaded": False,
                },
            )
        )
        seen_ids.add(identity)
        seen_urls.add(url)
        previous_day = day
    return entries
