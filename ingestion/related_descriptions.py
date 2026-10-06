"""One explicit MKiŚ catalogue link, not recursive enrichment or legal analysis."""

import hashlib
import json
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone

from ingestion.models import Item
from ingestion.normalization import title_key
from ingestion.publications import record_version
from ingestion.transport import FetchError

PROFILE = "mkis-related-consultation-v1"


def safe_url(url):
    p = urlsplit(url)
    return (
        p.scheme == "https"
        and p.netloc == "www.gov.pl"
        and p.path.startswith("/web/klimat/")
        and len(p.path.strip("/").split("/")) == 3
        and not p.query
        and not p.fragment
    )


def eligible(item):
    return (
        item.source.enabled
        and item.source.activation_approved
        and item.source.adapter in {"html", "web"}
        and item.source.url == "https://www.gov.pl/web/klimat/konsultacje-publiczne"
        and safe_url(item.url)
    )


def article(response, url, title, selector):
    if response.status != 200 or response.url != url:
        raise FetchError("Related document final URL mismatch")
    nodes = BeautifulSoup(response.body, "html.parser").select(selector)
    if len(nodes) != 1:
        raise FetchError("Related document article ambiguous")
    headings = nodes[0].find_all(["h1", "h2"], recursive=False)
    if len(headings) != 1 or title_key(headings[0].get_text()) != title_key(title):
        raise FetchError("Related document title mismatch")
    return nodes[0]


def fetch(item, client):
    if not eligible(item):
        raise FetchError("No approved related description profile")
    parent = article(
        client.get(item.url), item.url, item.title, "main article.article-area__article"
    )
    links = parent.select(".art-prev a[href]")
    if len(links) != 1:
        raise FetchError("Related link missing or ambiguous")
    url = urljoin(item.url, links[0]["href"])
    title = " ".join(links[0].get_text(" ", strip=True).split())
    if not safe_url(url) or url == item.url or not title:
        raise FetchError("Related link outside approved scope")
    child = article(client.get(url), url, title, "article#main-content")
    bodies = child.select(".editor-content")
    if len(bodies) != 1:
        raise FetchError("Related body missing or ambiguous")
    for node in bodies[0].select("script,style,nav,footer,[role=navigation]"):
        node.decompose()
    prose = " ".join(bodies[0].get_text(" ", strip=True).split())
    if len(prose) < 80:
        raise FetchError("Related description too short")
    return {
        "profile": PROFILE,
        "parent_url": item.url,
        "url": url,
        "title": title,
        "selector": "article#main-content .editor-content",
        "excerpt": prose[:600],
        "body_sha256": hashlib.sha256(prose.encode()).hexdigest(),
    }


@transaction.atomic
def save(item, evidence):
    current = Item.objects.select_for_update().select_related("source").get(pk=item.pk)
    if current.content_hash != item.content_hash or not eligible(current):
        raise FetchError("Parent changed during related fetch")
    if evidence.get("parent_url") != current.url or not safe_url(evidence.get("url", "")):
        raise FetchError("Related evidence scope mismatch")
    previous = current.metadata.get("related_description", {})
    if all(previous.get(key) == value for key, value in evidence.items()):
        return current
    record_version(current, origin="baseline")
    proof = {
        **evidence,
        "fetched_at": timezone.now().isoformat(),
        "listing_hash": previous.get("listing_hash", current.content_hash),
    }
    current.metadata = {**current.metadata, "related_description": proof}
    current.content_hash = hashlib.sha256(
        json.dumps([proof, current.summary], sort_keys=True).encode()
    ).hexdigest()
    current.save(update_fields=["metadata", "content_hash"])
    record_version(current, origin="related_excerpt")
    return current
