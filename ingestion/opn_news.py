"""Official OPN first-page news, using the reviewed shared park card template."""

from ingestion.bdpn_news import parse_cards

LEGACY = "https://www.ojcowskiparknarodowy.pl/"
URL = "https://opn.gov.pl/aktualnosci"
CONFIG = {"strict_endpoint": True, "opn_news": "first-page"}
NOTE = (
    "Aktualności Ojcowskiego Parku Narodowego z pierwszej strony co 6h. "
    "Tytuły, skróty i daty publikacji; bez pełnych artykułów, zdjęć, załączników, "
    "paginacji i pełnego archiwum. BIP, plany ochrony i konsultacje wymagają "
    "osobnego konektora."
)


def parse_news(source, response):
    return parse_cards(source, response, URL, CONFIG, "opn_initial_news_cards")
