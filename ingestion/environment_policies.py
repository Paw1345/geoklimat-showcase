"""Three official DG ENV thematic navigation groups; no child-page fetch."""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

LEGACY = "https://environment.ec.europa.eu/"
URL = "https://environment.ec.europa.eu/index_en"
CONFIG = {"strict_endpoint": True, "environment_policies": "thematic-groups"}
NOTE = (
    "Metadane trzech grup i tematów oficjalnego indeksu DG Environment, co 4h. "
    "Bez domyślnych dat publikacji, pobierania stron docelowych, pełnych dokumentów "
    "i analizy aktualnego stanu prawnego. Nie pełny katalog prawa ani aktualności."
)
GROUPS = {
    "Circular economy": "/strategy/circular-economy_en",
    "Pollution": "/strategy/zero-pollution-action-plan_en",
    "Nature and Biodiversity": "/strategy/biodiversity-strategy-2030_en",
}
EXTERNAL_LINK = (
    "https://commission.europa.eu/energy-climate-change-environment/standards-tools-and-labels/"
    "products-labelling-rules-and-requirements/ecodesign-sustainable-products-regulation_en"
)


def parse_policies(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("DG ENV endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("DG ENV encoding changed") from exc
    titles, headings, roots = (
        soup.select("head > title"),
        soup.select("h1.ecl-page-header__title"),
        soup.select("main#main-content"),
    )
    if (
        len(titles) != 1
        or text(titles[0].get_text()) != "Environment - Environment - European Commission"
        or len(headings) != 1
        or text(headings[0].get_text()) != "Environment"
        or len(roots) != 1
    ):
        raise FetchError("DG ENV page identity changed")
    cards = roots[0].select("article.ecl-navigation-list__item")
    if len(cards) != len(GROUPS):
        raise FetchError("DG ENV thematic group count changed")
    entries, categories, urls = [], set(), set()
    for card in cards:
        headers = card.select(".ecl-content-block__title > a")
        lists = card.select(".ecl-content-block__link-list")
        descriptions = card.select(".ecl-content-block__description")
        if len(headers) != 1 or len(lists) != 1 or len(descriptions) != 1:
            raise FetchError("DG ENV group fields changed")
        header = headers[0]
        category = text(header.get_text())
        group_url = urljoin(URL, header.get("href", ""))
        if (
            category not in GROUPS
            or category in categories
            or group_url != urljoin(URL, GROUPS[category])
        ):
            raise FetchError("DG ENV group label/link changed")
        categories.add(category)
        description = text(descriptions[0].get_text())
        if len(description) > 600:
            raise FetchError("DG ENV group description budget changed")
        rows = lists[0].select(":scope > li.ecl-content-block__link-item")
        if not 1 <= len(rows) <= 50 or len(rows) != len(lists[0].find_all(recursive=False)):
            raise FetchError("DG ENV thematic links budget/shape changed")
        topics = []
        for row in rows:
            links = row.select(":scope > a")
            if len(links) != 1 or len(row.select("a")) != 1:
                raise FetchError("DG ENV thematic link ambiguous")
            topics.append(links[0])
        if len(card.select("a")) != len(topics) + 1:
            raise FetchError("DG ENV unrecognized group link")
        for link, role in [(header, "group")] + [(a, "topic") for a in topics]:
            raw_url = link.get("href", "")
            url, title = urljoin(URL, raw_url), text(link.get_text())
            if (
                not raw_url
                or not 1 <= len(title) <= 1000
                or url in urls
                or (
                    not re.fullmatch(
                        r"https://environment\.ec\.europa\.eu/(?:strategy|topics)/"
                        r"[a-z0-9-]+(?:/[a-z0-9-]+)?_en",
                        url,
                    )
                    and url != EXTERNAL_LINK
                )
            ):
                raise FetchError("DG ENV thematic identity changed")
            urls.add(url)
            entries.append(
                Entry(
                    "dg-env-policy:" + url,
                    title,
                    url,
                    description if role == "group" else "",
                    "",
                    "",
                    {
                        "record_kind": "policy_catalogue_metadata",
                        "listing_scope": "dg_environment_thematic_groups",
                        "source_category": category,
                        "source_group_url": group_url,
                        "source_index_role": role,
                        "description_origin": "official_group_card"
                        if description and role == "group"
                        else "",
                        "target_page_downloaded": False,
                        "full_document_downloaded": False,
                        "current_legal_status_verified": False,
                        "funding_call_confirmed": False,
                    },
                )
            )
    if len(entries) > 150:
        raise FetchError("DG ENV combined thematic budget changed")
    return entries
