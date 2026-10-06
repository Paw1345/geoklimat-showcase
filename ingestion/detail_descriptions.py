"""Opt-in GUGiK/GIOS/INSPIRE excerpts; not full-text crawling or AI summaries."""

import hashlib
import json
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone

from ingestion.classification import classify_and_save
from ingestion.models import Item
from ingestion.normalization import title_key
from ingestion.publications import record_version
from ingestion.transport import FetchError

PROFILES = {"gugik": "gugik-article-v1", "gios": "gios-article-v1"}
INSPIRE_HOST = "knowledge-base.inspire.ec.europa.eu"
INSPIRE_PROFILE = "inspire-publication-v1"
INSPIRE_SELECTOR = "main#main-content #description + div .ecl"


def profile(item):
    if urlsplit(item.url).netloc == INSPIRE_HOST:
        return INSPIRE_PROFILE
    parts = urlsplit(item.url).path.strip("/").split("/")
    return PROFILES.get(parts[1], "") if len(parts) == 3 else ""


def eligible(item):
    source, target = urlsplit(item.source.url), urlsplit(item.url)
    if source.netloc == target.netloc == INSPIRE_HOST:
        return (
            item.source.enabled
            and item.source.activation_approved
            and item.source.adapter in {"html", "web"}
            and source.scheme == target.scheme == "https"
            and source.path == "/tools/document-library_en"
            and target.path.startswith("/publications/")
            and len(target.path.strip("/").split("/")) == 2
            and target.path.endswith("_en")
            and not target.query
            and not target.fragment
        )
    return (
        item.source.enabled
        and item.source.activation_approved
        and item.source.adapter in {"html", "web"}
        and source.scheme == target.scheme == "https"
        and source.netloc == target.netloc == "www.gov.pl"
        and any(
            source.path.startswith(f"/web/{institution}/")
            and target.path.startswith(f"/web/{institution}/")
            for institution in PROFILES
        )
        and len(target.path.strip("/").split("/")) == 3
        and not target.query
        and not target.fragment
    )


def extract(item, response):
    if not eligible(item) or response.url != item.url or response.status != 200:
        raise FetchError("Detail profile or final URL mismatch")
    soup = BeautifulSoup(response.body, "html.parser")
    inspire = profile(item) == INSPIRE_PROFILE
    articles = soup.select("main#main-content" if inspire else "article#main-content")
    if len(articles) != 1:
        raise FetchError("Detail article missing or ambiguous")
    article = articles[0]
    headings = soup.select("h1") if inspire else article.find_all(["h1", "h2"], recursive=False)
    bodies = soup.select(INSPIRE_SELECTOR) if inspire else article.select(".editor-content")
    if inspire and len(article.select("h2#description")) != 1:
        raise FetchError("Detail description section missing or ambiguous")
    if len(headings) != 1 or title_key(headings[0].get_text()) != title_key(item.title):
        raise FetchError("Detail title mismatch")
    if len(bodies) != 1:
        raise FetchError("Detail body missing or ambiguous")
    body = bodies[0]
    for node in body.select("script,style,nav,footer,[role=navigation]"):
        node.decompose()
    prose = " ".join(body.get_text(" ", strip=True).split())
    if len(prose) < 80:
        raise FetchError("Detail description too short")
    return prose[:600], hashlib.sha256(prose.encode()).hexdigest()


def refreshable(item):
    proof = item.metadata.get("description_detail")
    return (
        isinstance(proof, dict)
        and proof.get("profile") == profile(item)
        and proof.get("url") == item.url
        and isinstance(proof.get("listing_hash"), str)
        and len(proof["listing_hash"]) == 64
    )


@transaction.atomic
def save_description(item, summary, body_hash, refresh=False):
    current = Item.objects.select_for_update().select_related("source").get(pk=item.pk)
    if current.content_hash != item.content_hash or current.summary != item.summary:
        raise FetchError("Item changed during detail fetch")
    if (
        not eligible(current)
        or (refresh and not refreshable(current))
        or (not refresh and current.has_description)
    ):
        raise FetchError("Item is no longer eligible for enrichment")
    previous = current.metadata.get("description_detail", {}) if refresh else {}
    if refresh and current.summary == summary and previous.get("body_sha256") == body_hash:
        return current
    record_version(current, origin="baseline")
    proof = {
        "profile": profile(current),
        "url": current.url,
        "selector": (
            INSPIRE_SELECTOR
            if profile(current) == INSPIRE_PROFILE
            else "article#main-content .editor-content"
        ),
        "fetched_at": timezone.now().isoformat(),
        "body_sha256": body_hash,
        "listing_hash": previous["listing_hash"] if refresh else current.content_hash,
    }
    current.summary = summary
    current.metadata = {**current.metadata, "description_detail": proof}
    current.content_hash = hashlib.sha256(
        json.dumps([proof, summary], sort_keys=True).encode()
    ).hexdigest()
    current.save(update_fields=["summary", "metadata", "content_hash"])
    record_version(current, origin="detail_excerpt")
    classify_and_save(current)
    return current


def preserve_description(item, fields):
    """Retain an excerpt only while its originating list entry is unchanged."""
    related = item.metadata.get("related_description", {})
    if (
        isinstance(related, dict)
        and related.get("profile") == "mkis-related-consultation-v1"
        and related.get("listing_hash") == fields["content_hash"]
        and related.get("parent_url") == item.url == fields["url"]
        and item.title == fields["title"]
    ):
        fields.update(metadata=item.metadata, content_hash=item.content_hash)
    proof = item.metadata.get("description_detail", {})
    if (
        isinstance(proof, dict)
        and proof.get("profile") in {*PROFILES.values(), INSPIRE_PROFILE}
        and proof.get("listing_hash") == fields["content_hash"]
        and item.url == fields["url"]
        and item.title == fields["title"]
    ):
        fields.update(summary=item.summary, metadata=item.metadata, content_hash=item.content_hash)
