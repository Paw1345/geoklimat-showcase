"""Initial CBD update tabs, no PDF/notification/meeting/Load More requests."""

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

URL = "https://www.cbd.int/"
CONFIG = {"strict_endpoint": True, "cbd_updates": "initial-tabs"}
NOTE = (
    "Metadane początkowych kart czterech zakładek Updates CBD, co 12h. "
    "Komunikaty, powiadomienia, wydarzenia oraz rekomendacje/decyzje rozdzielone. "
    "Brak daty publikacji nie jest uzupełniany. Bez PDF, stron szczegółowych, "
    "Load More i pełnego archiwum."
)
GROUPS = ("secretariat", "notification", "event", "recommendation")


def source_day(raw):
    if not re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}", raw):
        raise FetchError("CBD source date format changed")
    try:
        return datetime.strptime(raw, "%d.%m.%Y").date().isoformat()
    except ValueError as exc:
        raise FetchError("CBD invalid source calendar date") from exc


def parse_updates(source, response):
    if (
        source.adapter != "web"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("CBD endpoint/profile changed")
    try:
        soup = BeautifulSoup(response.body.decode("utf-8"), "html.parser")
    except UnicodeDecodeError as exc:
        raise FetchError("CBD encoding changed") from exc
    titles = soup.select("head > title")
    headings = [h for h in soup.select("h2") if text(h.get_text()) == "Updates"]
    roots = soup.select(".block-cbd-utility.block-filters-block .view-filtered-items")
    if (
        len(titles) != 1
        or text(titles[0].get_text()) != "Home | Convention on Biological Diversity"
        or len(headings) != 1
        or len(roots) != 1
    ):
        raise FetchError("CBD page identity/container changed")
    root = roots[0]
    entries, keys = [], set()
    for group in GROUPS:
        blocks = [
            b
            for b in root.select(".limited-width-content." + group)
            if b.select(":scope > .post-wrapper")
        ]
        if len(blocks) != 1:
            raise FetchError("CBD update tab missing or ambiguous")
        cards = blocks[0].select(":scope > .post-wrapper")
        if not 1 <= len(cards) <= 100 or len(cards) != len(blocks[0].find_all(recursive=False)):
            raise FetchError("CBD update tab budget/children changed")
        for card in cards:
            if set(card.get("class", [])) != {"post-wrapper", "grid-sizer", group}:
                raise FetchError("CBD update kind conflict")
            headers, contents = (
                card.select(":scope > a.post-header"),
                card.select(":scope > a.post-content"),
            )
            if len(headers) != 1 or len(contents) != 1 or len(card.select("a")) != 2:
                raise FetchError("CBD update fields changed")
            header, content = headers[0], contents[0]
            url = urljoin(URL, content.get("href", ""))
            if not content.get("href") or urljoin(URL, header.get("href", "")) != url:
                raise FetchError("CBD header/title link conflict")
            spans = header.find_all("span", recursive=False)
            if (
                len(spans)
                not in (
                    {2, 4}
                    if group == "event"
                    else {4}
                    if group in {"notification", "recommendation"}
                    else {2}
                )
                or spans[0].get("class") != ["post-square"]
                or text(spans[0].get_text())
                or spans[1].get("class")
            ):
                raise FetchError("CBD date/header schema changed")
            raw_day = text(spans[1].get_text())
            published = source_day(raw_day) if raw_day else ""
            if bool(published) != (group in {"secretariat", "notification"}):
                raise FetchError("CBD publication date semantics changed")
            title = text(content.get_text())
            if not 1 <= len(title) <= 1000:
                raise FetchError("CBD title missing or too large")
            source_id = citation = document_type = ""
            if len(spans) == 4:
                if (
                    spans[2].get("class") != ["post-slashes"]
                    or text(spans[2].get_text()) != "//"
                    or spans[3].get("class") != ["post-title"]
                ):
                    raise FetchError("CBD source identifier schema changed")
                source_id = text(spans[3].get_text())
            if group == "secretariat":
                match = re.fullmatch(
                    r"https://www\.cbd\.int/doc/(press|speech)/(20[0-9]{2})/"
                    r"(?:pr|sp)-[a-z0-9-]{1,250}-en\.pdf",
                    url,
                )
                if not match or match[2] != published[:4]:
                    raise FetchError("CBD secretariat document link changed")
                if not url.rsplit("/", 1)[1].startswith("pr-" if match[1] == "press" else "sp-"):
                    raise FetchError("CBD document type/path conflict")
                document_type = match[1]
                key = url
            elif group == "notification":
                if (
                    not re.fullmatch(r"20[0-9]{2}-[0-9]{3}", source_id)
                    or url != URL + "notifications/" + source_id
                    or source_id[:4] != published[:4]
                ):
                    raise FetchError("CBD notification ID/date/link conflict")
                document_type, key = "notification", source_id
            elif group == "event":
                match = re.fullmatch(
                    r"https://www\.cbd\.int/meetings/([0-9]{6}|[A-Z][A-Z0-9-]{1,60})", url
                )
                if not match or (source_id and source_id != match[1]):
                    raise FetchError("CBD event meeting identity changed")
                source_id, document_type, key = match[1], "event", match[1]
            else:
                if (
                    not re.fullmatch(r"[A-Z][A-Z0-9]*-(?:MOP-)?[0-9]{2}", source_id)
                    or url != URL + "meetings/" + source_id
                ):
                    raise FetchError("CBD recommendation meeting identity changed")
                match = re.fullmatch(
                    r"((Recommendation|Decision) (?:[A-Z]+-)?[1-9][0-9]*/[1-9][0-9]*"
                    r"(?: [A-Z](?: and [A-Z])?)?) - .+",
                    title,
                )
                if not match:
                    raise FetchError("CBD recommendation citation changed")
                citation, document_type = match[1], match[2]
                key = source_id + ":" + citation
            key = "cbd:" + group + ":" + key
            if key in keys:
                raise FetchError("CBD duplicate update identity")
            keys.add(key)
            entries.append(
                Entry(
                    key,
                    title,
                    url,
                    "",
                    published,
                    "",
                    {
                        "record_kind": "cbd_update_metadata",
                        "listing_scope": "cbd_initial_update_tabs",
                        "source_update_kind": group,
                        "source_document_type": document_type,
                        "source_publication_date_text": raw_day,
                        "source_record_id": source_id,
                        "source_citation": citation,
                        "full_document_downloaded": False,
                        "full_archive_downloaded": False,
                        "current_legal_status_verified": False,
                        "funding_call_confirmed": False,
                    },
                )
            )
    if len(entries) > 200:
        raise FetchError("CBD total update budget changed")
    return entries
