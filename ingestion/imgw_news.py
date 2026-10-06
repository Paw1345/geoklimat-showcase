"""Official WordPress RSS metadata; no synthetic updated date or article body."""

import re
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from ingestion.adapters import Entry, text
from ingestion.transport import FetchError

LEGACY = "https://imgw.pl/"
URL = "https://imgw.pl/feed/"
CONFIG = {"strict_endpoint": True, "imgw_news": "official-rss"}
NOTE = (
    "Metadane oficjalnego RSS IMGW-PIB, co 1h. Wiadomości, nie pomiary "
    "ani ostrzeżenia pogodowe. Daty z pubDate; bez domyślnej daty aktualizacji, "
    "zapisu treści artykułów, pobierania załączników i pełnego archiwum."
)


def one(node, tag):
    values = node.findall(tag)
    if len(values) != 1 or len(values[0]) or not isinstance(values[0].text, str):
        raise FetchError("IMGW RSS field missing/ambiguous")
    return values[0].text.strip(), values[0]


def parse_news(source, response):
    if (
        source.adapter != "feed"
        or source.url != URL
        or source.config != CONFIG
        or response.status != 200
        or response.url != URL
    ):
        raise FetchError("IMGW RSS endpoint/profile changed")
    try:
        body = response.body.decode("utf-8")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", body, re.I):
            raise FetchError("IMGW RSS DTD/entity declaration refused")
        root = ET.fromstring(body)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise FetchError("IMGW RSS encoding/XML changed") from exc
    channels = root.findall("channel")
    if root.tag != "rss" or root.get("version") != "2.0" or len(channels) != 1:
        raise FetchError("IMGW RSS channel schema changed")
    channel = channels[0]
    if (
        one(channel, "title")[0] != "IMGW-PIB"
        or one(channel, "link")[0] != "https://imgw.pl"
        or one(channel, "language")[0] != "pl-PL"
    ):
        raise FetchError("IMGW RSS channel identity changed")
    self_links = channel.findall("{http://www.w3.org/2005/Atom}link")
    if (
        len(self_links) != 1
        or self_links[0].get("href") != URL
        or self_links[0].get("rel") != "self"
        or self_links[0].get("type") != "application/rss+xml"
    ):
        raise FetchError("IMGW RSS self link changed")
    rows = channel.findall("item")
    if not 1 <= len(rows) <= 100:
        raise FetchError("IMGW RSS item budget changed")
    entries, ids, urls = [], set(), set()
    for row in rows:
        raw_title, _ = one(row, "title")
        title = text(raw_title)
        url, _ = one(row, "link")
        guid, guid_node = one(row, "guid")
        identity = re.fullmatch(r"https://imgw\.pl/\?p=([1-9][0-9]{0,11})", guid)
        if (
            not identity
            or guid_node.get("isPermaLink") != "false"
            or not re.fullmatch(r"https://imgw\.pl/[a-z0-9%-]{1,500}/", url)
            or not 1 <= len(title) <= 1000
            or guid in ids
            or url in urls
        ):
            raise FetchError("IMGW RSS item identity changed")
        raw_date, _ = one(row, "pubDate")
        if not re.fullmatch(
            r"[A-Z][a-z]{2}, [0-9]{2} [A-Z][a-z]{2} [0-9]{4} "
            r"[0-9]{2}:[0-9]{2}:[0-9]{2} [+-][0-9]{4}",
            raw_date,
        ):
            raise FetchError("IMGW RSS publication date format changed")
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError, OverflowError) as exc:
            raise FetchError("IMGW RSS publication date invalid") from exc
        if (
            published.utcoffset() is None
            or published.strftime("%a, %d %b %Y %H:%M:%S %z") != raw_date
        ):
            raise FetchError("IMGW RSS publication date/day conflict")
        categories = []
        for category in row.findall("category"):
            if len(category) or not isinstance(category.text, str):
                raise FetchError("IMGW RSS category schema changed")
            label = text(category.text)
            if not 1 <= len(label) <= 200 or label in categories:
                raise FetchError("IMGW RSS category identity changed")
            categories.append(label)
        if not 1 <= len(categories) <= 30:
            raise FetchError("IMGW RSS category budget changed")
        ids.add(guid)
        urls.add(url)
        entries.append(
            Entry(
                "imgw-post:" + identity[1],
                title,
                url,
                "",
                published.isoformat(),
                "",
                {
                    "record_kind": "news_notice",
                    "listing_scope": "imgw_official_rss",
                    "source_record_id": identity[1],
                    "source_guid": guid,
                    "source_publication_date_text": raw_date,
                    "source_categories": categories,
                    "article_body_stored": False,
                    "target_page_downloaded": False,
                    "attachments_downloaded": False,
                    "measurement_record": False,
                    "weather_alert_confirmed": False,
                    "funding_call_confirmed": False,
                },
            )
        )
    return entries
