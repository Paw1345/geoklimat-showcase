"""Official DG CLIMA policy-index cards; source descriptions, not legal analysis."""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

LEGACY = "https://climate.ec.europa.eu/"
URL = "https://climate.ec.europa.eu/areas-action_en"
CONFIG = {"strict_endpoint": True, "clima_policies": "areas-action"}
NOTE = (
    "Metadane pięciu sekcji oficjalnego katalogu DG CLIMA Areas of action, co 4h. "
    "Tytuły i krótkie opisy kart źródłowych; bez domyślnych dat publikacji, "
    "pełnych dokumentów, stron docelowych i analizy aktualnego stanu prawnego."
)
GROUPS = (
    "Strategies & Governance",
    "Mitigation",
    "Adaptation & Resilience",
    "Funding & Innovation",
    "Cooperation & Engagement",
)
EXTERNAL_LINKS = {
    "https://commission.europa.eu/strategy-and-policy/priorities-2019-2024/european-green-deal_en",
    "https://commission.europa.eu/topics/competitiveness/clean-industrial-deal_en",
    "https://eu-mayors.ec.europa.eu/en/home",
    "https://climate-pact.europa.eu/index_en",
}


def parse_policies(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("DG CLIMA endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("DG CLIMA encoding changed") from exc
    titles, headings, roots = (
        soup.select("head > title"),
        soup.select("h1.ecl-page-header__title"),
        soup.select("main#main-content"),
    )
    if (
        len(titles) != 1
        or text(titles[0].get_text()) != "Areas of action - Climate Action - European Commission"
        or len(headings) != 1
        or text(headings[0].get_text()) != "Areas of action"
        or len(roots) != 1
    ):
        raise FetchError("DG CLIMA page identity changed")
    root = roots[0]
    sections = root.select('[id^="block-navigationeuaction"]')
    if len(sections) != len(GROUPS):
        raise FetchError("DG CLIMA policy sections changed")
    entries, urls, seen_cards = [], set(), set()
    for number, category in enumerate(GROUPS, 1):
        blocks = root.select(f"#block-navigationeuaction{number}")
        if len(blocks) != 1:
            raise FetchError("DG CLIMA policy section missing")
        block = blocks[0]
        labels = block.select(":scope > h2")
        cards = block.select("article.ecl-card")
        if len(labels) != 1 or text(labels[0].get_text()) != category or not 1 <= len(cards) <= 50:
            raise FetchError("DG CLIMA section label/card budget changed")
        for card in cards:
            if id(card) in seen_cards:
                raise FetchError("DG CLIMA nested policy card")
            seen_cards.add(id(card))
            links = card.select(".ecl-content-block__title > a")
            descriptions = card.select(".ecl-content-block__description")
            if len(links) != 1 or len(card.select("a")) != 1 or len(descriptions) != 1:
                raise FetchError("DG CLIMA policy fields changed")
            raw_url = links[0].get("href", "")
            url = urljoin(URL, raw_url)
            title, description = text(links[0].get_text()), text(descriptions[0].get_text())
            if (
                not raw_url
                or url in urls
                or not 1 <= len(title) <= 1000
                or not 1 <= len(description) <= 600
                or (
                    not re.fullmatch(
                        r"https://climate\.ec\.europa\.eu/areas-action/[a-z0-9-]{1,250}_en",
                        url,
                    )
                    and url not in EXTERNAL_LINKS
                )
            ):
                raise FetchError("DG CLIMA policy identity/description changed")
            urls.add(url)
            entries.append(
                Entry(
                    "dg-clima-policy:" + url,
                    title,
                    url,
                    description,
                    "",
                    "",
                    {
                        "record_kind": "policy_catalogue_metadata",
                        "listing_scope": "dg_clima_areas_action_cards",
                        "source_category": category,
                        "description_origin": "official_index_card",
                        "target_page_downloaded": False,
                        "full_document_downloaded": False,
                        "current_legal_status_verified": False,
                        "funding_call_confirmed": False,
                    },
                )
            )
    if len(entries) > 150 or len(seen_cards) != len(root.select("article.ecl-card")):
        raise FetchError("DG CLIMA total policy budget/unassigned cards changed")
    return entries
