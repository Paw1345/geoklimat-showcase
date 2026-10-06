"""Metadata of the official protection document catalogue, not legal verification."""

import re
from uuid import UUID

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.bdpn_news import single
from ingestion.transport import FetchError

URL = "https://opn.gov.pl/dzialalnosc-ochronna"
CONFIG = {"strict_endpoint": True, "opn_documents": "protection-catalogue"}
SLUG = "registry-151-protection-documents"
ISAP = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20210001432"
NOTE = (
    "Katalog dokumentów działalności ochronnej OPN co 24h: nazwy, linki i "
    "deklarowane rozmiary PDF oraz wskazany przez park plan w ISAP. "
    "Bez pobierania plików, weryfikacji ich treści i obowiązywania prawa. "
    "Daty publikacji niepodane; bez pełnego BIP, konsultacji i archiwum."
)


def parse_documents(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("OPN documents endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("OPN documents encoding changed") from exc
    main = single(soup, "main#main-content")
    if text(single(main, "h1").get_text()) != "Działalność ochronna":
        raise FetchError("OPN documents heading changed")
    downloads = single(main, "section.downloads")
    if text(single(downloads, "h2").get_text()) != "Dokumenty związane z działalnością ochronną":
        raise FetchError("OPN documents catalogue identity changed")
    listing = single(downloads, "ul.downloads__list")
    rows = listing.select(":scope > li.downloads__item")
    if not 1 <= len(rows) <= 100 or len(listing.select("li")) != len(rows):
        raise FetchError("OPN documents budget/schema changed")
    entries, seen = [], set()
    for row in rows:
        name = single(row, "a.downloads__item-name")
        action = single(row, "a.downloads__item-action")
        details = single(row, ".downloads__item-details")
        if len(row.select("a")) != 2:
            raise FetchError("OPN documents unexpected links")
        title, path = text(name.get_text()), name.get("href", "")
        match = re.fullmatch(r"/pliki-do-pobrania/otworz/([a-f0-9-]{36})\.pdf", path)
        if not match:
            raise FetchError("OPN documents file URL changed")
        try:
            identifier = str(UUID(match[1]))
        except ValueError as exc:
            raise FetchError("OPN documents identity invalid") from exc
        size = text(details.get_text(" "))
        if (
            identifier != match[1]
            or identifier in seen
            or action.get("href") != f"/pliki-do-pobrania/pobierz/{identifier}.pdf"
            or not 1 <= len(title) <= 1000
            or not re.fullmatch(r"pdf - [0-9]{1,6}(?:\.[0-9]{1,2})? (?:KB|MB|GB)", size)
        ):
            raise FetchError("OPN documents metadata conflict/duplicate")
        seen.add(identifier)
        entries.append(
            Entry(
                key=identifier,
                title=title,
                url="https://opn.gov.pl" + path,
                metadata={
                    "listing_scope": "opn_protection_catalogue",
                    "source_document_type": "PDF",
                    "source_file_size_text": size,
                    "document_body_stored": False,
                    "legal_status_verified": False,
                    "publication_date_unknown": True,
                },
            )
        )
    plan = single(main, "section.text-with-links")
    if text(single(plan, "h2").get_text()) != "Plan ochrony dla Ojcowskiego Parku Narodowego":
        raise FetchError("OPN plan section changed")
    link = single(plan, "a.text-with-links__item")
    if (
        len(plan.select("a")) != 1
        or link.get("href") != ISAP
        or text(single(link, ".text-with-links__item__content").get_text())
        != "Pełna wersja planu ochrony"
    ):
        raise FetchError("OPN plan reference changed; review required")
    entries.append(
        Entry(
            key=ISAP,
            title="Plan ochrony dla Ojcowskiego Parku Narodowego — odnośnik ISAP",
            url=ISAP,
            metadata={
                "listing_scope": "opn_protection_catalogue",
                "source_document_type": "Odnośnik ISAP",
                "source_reference": "WDU20210001432",
                "document_body_stored": False,
                "legal_status_verified": False,
                "publication_date_unknown": True,
            },
        )
    )
    return entries
