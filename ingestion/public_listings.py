"""Fail-closed parsers for six reviewed public P0 listings."""

import hashlib
import json
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

from ingestion.adapters import Entry
from ingestion.transport import FetchError

SELECTORS = {
    "geoportal_news": ("main article.post", "h2.entry-title a[href]", ".entry-summary"),
    "geoportal_services": ("main li.wp-block-post", "h2 a[href]", ".wp-block-post-excerpt"),
    "ekoportal": ("a.inv-news-list-link[href]", None, ".news-info-teaser"),
    "kobize": ("#right-column article", "h4 a[href]", "p"),
    "inspire_documents": (
        ".ecl-content-item",
        ".ecl-content-block__title a[href]",
        ".ecl-content-block__description",
    ),
}
POLISH_MONTHS = (
    "styczeń luty marzec kwiecień maj czerwiec lipiec sierpień "
    "wrzesień październik listopad grudzień"
).split()


def publication_date(row, listing):
    if listing == "ekoportal":
        parts = [cell.get_text(strip=True) for cell in row.select(".news-tabletype span")]
        try:
            return date(
                int(parts[2]), POLISH_MONTHS.index(parts[1].casefold()) + 1, int(parts[0])
            ).isoformat()
        except (IndexError, ValueError) as exc:
            raise FetchError("Ekoportal publication date schema changed") from exc
    if listing == "kobize":
        match = re.search(
            r"Data publikacji:\s*(\d{2}\.\d{2}\.\d{4})", row.get_text(" ", strip=True)
        )
        if not match:
            raise FetchError("KOBiZE publication date missing")
        return match[1]
    if listing == "inspire_documents":
        for node in row.select(".ecl-content-block__primary-meta-item"):
            try:
                return (
                    datetime.strptime(node.get_text(" ", strip=True), "%d %B %Y").date().isoformat()
                )
            except ValueError:
                continue
        raise FetchError("INSPIRE document date missing or changed")
    node = row.select_one("time, .date")
    return node.get("datetime", node.get_text(" ", strip=True)) if node else ""


def parse_listing(source, soup, response):
    listing = source.config["listing"]
    if listing == "nfosigw_calls":
        return funding_calls(soup, response.url)
    if listing not in SELECTORS:
        raise FetchError("Unknown reviewed listing profile")
    selector, link_selector, summary_selector = SELECTORS[listing]
    rows = soup.select(selector)
    if not rows:
        raise FetchError("Reviewed listing empty or structure changed")
    entries = []
    seen = set()
    for row in rows:
        link = row.select_one(link_selector) if link_selector else row
        if not link or not link.get("href"):
            raise FetchError("Reviewed listing title link missing")
        title = link.get("title") if listing == "ekoportal" else None
        title = title or link.get_text(" ", strip=True)
        summary = row.select_one(summary_selector)
        url = urljoin(response.url, link["href"])
        kind = {
            "geoportal_services": "service_directory",
            "inspire_documents": "technical_document",
        }.get(listing, "news")
        published = publication_date(row, listing)
        identity = url
        if listing == "ekoportal" and not urlsplit(url).path.startswith("/aktualnosci/szczegoly/"):
            # Several dated notices point to the same category page, not an article.
            identity = url + "\n" + published
            kind = "news_notice"
        if identity in seen:
            raise FetchError("Ambiguous listing record identity")
        seen.add(identity)
        entries.append(
            Entry(
                identity,
                title,
                url,
                summary.get_text(" ", strip=True) if summary else "",
                published,
                metadata={"record_kind": kind},
            )
        )
    return entries


def table_grid(table):
    """Expand merged cells with strict bounds; retain cell objects, not personal data."""
    grid, occupied = [], {}
    rows = table.find_all("tr")
    if len(rows) > 1000:
        raise FetchError("Funding table row budget exceeded")
    for y, row in enumerate(rows):
        x = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            while (y, x) in occupied:
                x += 1
            try:
                height, width = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
            except ValueError as exc:
                raise FetchError("Invalid funding table span") from exc
            if not (
                1 <= height <= 1000
                and 1 <= width <= 30
                and x + width <= 30
                and y + height <= len(rows)
            ):
                raise FetchError("Funding table span budget exceeded")
            for dy in range(height):
                for dx in range(width):
                    if (y + dy, x + dx) in occupied:
                        raise FetchError("Overlapping funding table cells")
                    occupied[y + dy, x + dx] = cell
            x += width
        width = max((col for line, col in occupied if line == y), default=-1) + 1
        grid.append([occupied.get((y, col)) for col in range(width)])
    return grid


def funding_calls(soup, url):
    entries, seen = [], set()
    for table in soup.select("main table, article table"):
        grid = table_grid(table)
        header_seen = False
        for cells in grid:
            values = [
                " ".join(cell.get_text(" ", strip=True).split()) if cell else "" for cell in cells
            ]
            if not any(values):
                continue
            if len(values) == 10 and values[0] == "Nr PP":
                if not (
                    values[2].startswith("Nazwa programu")
                    and "rozpoczęcia" in values[4]
                    and "zakończenia" in values[5]
                    and "Stan naboru" in values[6]
                    and "Oznaczenie" in values[7]
                ):
                    raise FetchError("Funding table columns changed")
                header_seen = True
                continue
            if not header_seen or len(set(id(cell) for cell in cells)) <= 2:
                continue  # Section headings are not calls.
            if len(values) != 10 or not values[2]:
                raise FetchError("Funding table row schema changed")
            # Exclude coordinator/contact cells and free-form remarks entirely.
            fields = dict(
                zip(
                    (
                        "program_code",
                        "name",
                        "mode",
                        "start_text",
                        "end_text",
                        "status_text",
                        "call_code",
                    ),
                    (values[0], *values[2:8]),
                    strict=True,
                )
            )
            identity = json.dumps(
                [fields[key] for key in ("program_code", "name", "mode", "call_code")],
                ensure_ascii=False,
            )
            if identity in seen:
                raise FetchError("Ambiguous funding call identity; no partial table committed")
            seen.add(identity)
            summary = (
                f"Od: {fields['start_text'] or 'nie podano'}; "
                f"do: {fields['end_text'] or 'nie podano'}; "
                f"stan: {fields['status_text'] or 'nie podano'}."
            )
            digest = hashlib.sha256(
                json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            entries.append(
                Entry(
                    identity,
                    "Nabór: " + fields["name"],
                    url,
                    summary,
                    metadata={
                        "record_kind": "funding_call",
                        "funding": fields,
                        "document_hash": digest,
                    },
                )
            )
    if not entries:
        raise FetchError("No recognized funding schedule rows")
    return entries
