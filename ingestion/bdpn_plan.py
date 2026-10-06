"""BIP plan page and attachment metadata; never fetch attachment bytes."""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.bdpn_news import single
from ingestion.transport import FetchError

URL = "https://bdpn.gov.pl/bip/plan-ochrony"
CONFIG = {"strict_endpoint": True, "bdpn_plan": "bip-page"}
SLUG = "registry-144-protection-plan"
ISAP = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20220001919"
GROUP = "Mapa przeglądowa Bieszczadzkiego Parku Narodowego (stan na 1.IX.2022)"
NOTE = (
    "Sekcja Plan ochrony BIP BDPN co24h: metryka strony, odnośnik ISAP i "
    "metadane załączników PDF/SHP-ZIP. Bez plików, rozpakowywania ZIP, "
    "weryfikacji aktu, pełnego BIP i konsultacji. Czasy źródłowe bez strefy "
    "zachowane jako tekst; publikacja z dokładnością do dnia."
)


def fields(root):
    result = {}
    rows = root.select(":scope > .bip-accordion__item__content__item")
    if not 1 <= len(rows) <= 30:
        raise FetchError("BDPN BIP metric budget changed")
    for row in rows:
        parts = row.select(":scope > div")
        if len(parts) != 2:
            raise FetchError("BDPN BIP metric structure changed")
        key, value = [text(p.get_text(" ")) for p in parts]
        if not key or key in result:
            raise FetchError("BDPN BIP duplicate metric")
        result[key] = value
    return result


def source_date(raw, timestamp=False):
    pattern = r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}"
    if timestamp:
        pattern += r" [0-9]{2}:[0-9]{2}:[0-9]{2}"
    if not isinstance(raw, str) or not re.fullmatch(pattern, raw):
        raise FetchError("BDPN BIP date format changed")
    try:
        return datetime.strptime(raw, "%d.%m.%Y %H:%M:%S" if timestamp else "%d.%m.%Y")
    except ValueError as exc:
        raise FetchError("BDPN BIP calendar date invalid") from exc


def parse_plan(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("BDPN BIP endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("BDPN BIP encoding changed") from exc
    main = single(soup, "main#main-content")
    if text(single(main, "h1").get_text()) != "Plan ochrony - BIP Bieszczadzki Park Narodowy":
        raise FetchError("BDPN BIP page identity changed")
    root = single(main, "section.bip-block-content")
    if text(single(root, ".bip-title__content__title").get_text()) != "Plan ochrony":
        raise FetchError("BDPN BIP title changed")
    content = single(root, ":scope > .bip__text")
    reference = single(content, "a")
    title = text(reference.get_text())
    if reference.get("href") != ISAP or not 1 <= len(title) <= 1000:
        raise FetchError("BDPN BIP ISAP reference changed; review required")
    metric = fields(
        single(root, ":scope > .bip-accordion > .bip-accordion__item > #bip-accordion-item-0")
    )
    if metric.get("Podmiot udostępniający informację:") != "Bieszczadzki Park Narodowy":
        raise FetchError("BDPN BIP publisher changed")
    published = source_date(metric.get("Data publikacji:"))
    original = source_date(metric.get("Data pierwotnej publikacji:"), True)
    modified = source_date(metric.get("Data ostatniej modyfikacji:"), True)
    if not original.date() <= published.date() <= modified.date():
        raise FetchError("BDPN BIP metric chronology conflict")
    common = {
        "listing_scope": "bdpn_bip_plan",
        "document_body_stored": False,
        "legal_status_verified": False,
        "source_timezone_unknown": True,
    }
    entries = [
        Entry(
            key=URL,
            title="BDPN — Plan ochrony (strona BIP)",
            url=URL,
            published=published.date().isoformat(),
            metadata={
                **common,
                "source_document_type": "Strona BIP",
                "source_publication_date_text": metric["Data publikacji:"],
                "source_original_publication_text": metric["Data pierwotnej publikacji:"],
                "source_last_modification_text": metric["Data ostatniej modyfikacji:"],
                "source_reference_url": ISAP,
                "source_reference_title": title,
                "source_reference": "WDU20220001919",
            },
        )
    ]
    group = single(root, ":scope > .bip-attachments")
    if text(single(group, "h2").get_text()) != GROUP:
        raise FetchError("BDPN BIP attachment group changed")
    container = single(group, ":scope > .bip-attachments__container")
    cards = container.select(":scope > .bip-attachments__container__item")
    if not 1 <= len(cards) <= 100:
        raise FetchError("BDPN BIP attachment budget changed")
    seen = set()
    for card in cards:
        a = single(card, "a.bip-attachments__container__item__title")
        path = a.get("href", "")
        match = re.fullmatch(r"/uploads/files/([a-f0-9]{22})\.(pdf|zip)", path)
        label = text(a.get_text(" "))
        if not match or path in seen or len(card.select("a")) != 1:
            raise FetchError("BDPN BIP attachment identity changed/duplicate")
        kind = "PDF" if match[2] == "pdf" else "SHP (ZIP)"
        if label != ("W formacie PDF" if match[2] == "pdf" else "W formacie SHP"):
            raise FetchError("BDPN BIP extension/label conflict")
        size = text(single(card, ".bip-attachments__container__item__size").get_text())
        if not re.fullmatch(
            ("PDF" if match[2] == "pdf" else "ZIP")
            + r" - [0-9]{1,6}(?:\.[0-9]{1,2})? (?:KB|MB|GB)",
            size,
        ):
            raise FetchError("BDPN BIP declared size changed")
        info = fields(single(card, ".bip-accordion__item__content"))
        if info.get("Podmiot udostępniający informację:") != "Bieszczadzki Park Narodowy":
            raise FetchError("BDPN BIP attachment publisher changed")
        raw_date = info.get("Czas publikacji:")
        stamp = source_date(raw_date, True)
        seen.add(path)
        item_url = "https://bdpn.gov.pl" + path
        entries.append(
            Entry(
                key=item_url,
                title=GROUP + " — " + kind,
                url=item_url,
                published=stamp.date().isoformat(),
                metadata={
                    **common,
                    "source_document_type": kind,
                    "source_publication_date_text": raw_date,
                    "source_file_size_text": size,
                    "source_map_state_text": GROUP,
                },
            )
        )
    return entries
