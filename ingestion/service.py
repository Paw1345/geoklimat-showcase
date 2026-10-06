import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from django.db import connection, transaction
from django.utils import timezone

from ingestion.adapters import date_value, parse
from ingestion.classification import classify_and_save
from ingestion.models import IngestionRun, Item, Source
from ingestion.publications import enrich, suggest_duplicates
from ingestion.sopo_policy import ERROR as SOPO_ERROR
from ingestion.sopo_policy import blocked_source as sopo_blocked_source
from ingestion.source_policy import SourcePolicyChanged, check_policy, policy_snapshot
from ingestion.transport import Client, FetchError, ProviderPolicyBlocked

logger = logging.getLogger(__name__)


@contextmanager
def batch_lock():
    # Session-scoped lock is released by PostgreSQL even if the worker is killed.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(726042)")
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(726042)")


def fetch_entries(source, client, conditional=True):
    if sopo_blocked_source(source):
        raise ProviderPolicyBlocked(SOPO_ERROR)
    if source.adapter == "stac" and source.config.get("mode") == "ecmwf_ecds_collections":
        from ingestion.ecmwf_catalogue import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "stac" and source.config.get("mode") == "clms_cdse_collections":
        from ingestion.clms_catalogue import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "json" and source.config.get("mode") == "ems_initial_activations":
        from ingestion.ems_catalogue import fetch_catalogue

        return fetch_catalogue(source, client, conditional=conditional)
    if source.adapter == "json" and source.config.get("mode") == "hys_environment":
        from ingestion.hys_catalogue import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "eli" and "{year}" in source.url:
        from ingestion.eli_catalogue import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "json" and source.config.get("mode") == "inspire_codes":
        from ingestion.inspire_codes import fetch_codes

        return fetch_codes(source, client)
    if source.adapter == "json" and source.config.get("mode") == "gios_stations_v1":
        from ingestion.gios_stations import fetch_stations

        return fetch_stations(source, client)
    if source.adapter == "json" and source.config.get("mode") == "inspire_schemes":
        from ingestion.inspire_vocabularies import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "json" and source.config.get("mode") == "funding_reference":
        from ingestion.funding_reference import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "json" and source.config.get("mode") == "jrc_environment":
        from ingestion.jrc_catalogue import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "json" and source.config.get("mode") == "cellar_metadata":
        from ingestion.cellar import fetch_metadata

        entries, rejected, response = fetch_metadata(source, client)
        stable = [(entry.key, entry.title, entry.metadata) for entry in entries]
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        return entries, rejected, response, digest
    if source.adapter == "stac" and source.config.get("mode") == "marine_products":
        from ingestion.marine import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "watch" and source.config.get("mode") == "eurostat_toc":
        from ingestion.eurostat import fetch_catalogue

        return fetch_catalogue(source, client)
    if source.adapter == "json" and source.config.get("mode") == "dane_gov":
        from ingestion.open_data import fetch_datasets

        return fetch_datasets(source, client)
    if source.adapter == "web" and source.config.get("pagination"):
        return fetch_web_pages(source, client)
    if source.adapter == "stac":
        entries, rejected, fingerprints, seen = [], 0, [], set()
        identities, expected_total = set(), None
        url = source.url
        for _ in range(50):
            if url in seen or urlsplit(url).netloc != urlsplit(source.url).netloc:
                raise FetchError("STAC pagination loop or cross-host link")
            seen.add(url)
            response = client.get(url)
            data = json.loads(response.body)
            if source.config.get("verify_total"):
                original, actual = urlsplit(source.url), urlsplit(response.url)
                if (actual.scheme, actual.netloc, actual.path) != (
                    "https",
                    original.netloc,
                    original.path,
                ):
                    raise FetchError("STAC catalogue left configured endpoint")
                total, returned = data.get("numberMatched"), data.get("numberReturned")
                if (
                    type(total) is not int
                    or total < 0
                    or type(returned) is not int
                    or returned != len(data.get("collections", []))
                    or (expected_total is not None and expected_total != total)
                ):
                    raise FetchError("STAC catalogue count missing, changed or inconsistent")
                expected_total = total
            links = [link for link in data.get("links", []) if link.get("rel") == "next"]
            if len(links) > 1 or any(link.get("method", "GET") != "GET" for link in links):
                raise FetchError("Unsupported STAC pagination")
            data["links"] = [link for link in data.get("links", []) if link.get("rel") != "next"]
            from ingestion.transport import Response

            page_response = Response(
                response.status, json.dumps(data).encode(), response.headers, response.url
            )
            batch, bad = parse(source, page_response)
            for entry in batch:
                if entry.key in identities:
                    raise FetchError("Repeated STAC collection across pages")
                identities.add(entry.key)
            entries.extend(batch)
            rejected += bad
            fingerprints.append(hashlib.sha256(response.body).hexdigest())
            if not links:
                if expected_total is not None and len(entries) != expected_total:
                    raise FetchError("STAC catalogue incomplete; no partial batch committed")
                return (
                    entries,
                    rejected,
                    response,
                    hashlib.sha256("".join(fingerprints).encode()).hexdigest(),
                )
            url = urljoin(response.url, links[0]["href"])
            if source.config.get("verify_total") and (
                urlsplit(url).scheme != "https" or urlsplit(url).path != urlsplit(source.url).path
            ):
                raise FetchError("STAC catalogue pagination left configured endpoint")
        raise FetchError("STAC page budget exceeded; no partial batch committed")
    if source.adapter == "json" and source.config.get("mode") != "bdl_collections":
        from ingestion.extended_adapters import json_rows

        entries, rejected, fingerprints = [], 0, []
        size = min(500, max(1, int(source.config.get("page_size", 100))))
        maximum = min(30, max(1, int(source.config.get("max_pages", 10))))
        parsed = urlsplit(source.url)
        for page in range(maximum):
            params = dict(parse_qsl(parsed.query))
            if source.config.get("mode") == "gios":
                params.update(page=page, size=size)
            else:
                params.update(offset=page * size, limit=size, sort_by="-lastModified")
            url = urlunsplit(parsed._replace(query=urlencode(params)))
            response = client.get(url)
            fingerprints.append(hashlib.sha256(response.body).hexdigest())
            rows = json_rows(source, response.body)
            batch, bad = parse(source, response)
            entries.extend(batch)
            rejected += bad
            if len(rows) < size:
                return (
                    entries,
                    rejected,
                    response,
                    hashlib.sha256("".join(fingerprints).encode()).hexdigest(),
                )
        raise FetchError("Pagination budget exceeded; no partial batch committed")
    headers = {}
    if conditional and source.adapter != "eli":
        if source.etag:
            headers["If-None-Match"] = source.etag
        if source.last_modified:
            headers["If-Modified-Since"] = source.last_modified
    urls = [source.url]
    if "{year}" in source.url:
        year = timezone.now().year
        urls = [source.url.replace("{year}", str(value)) for value in (year, year - 1)]
    entries, rejected, fingerprints = [], 0, []
    for url in urls:
        response = client.get(url, headers)
        if source.config.get("strict_endpoint") and response.url != url:
            raise FetchError("Response left the configured endpoint")
        if urlsplit(url).hostname == "www.gov.pl" and "/web/" in url:
            prefix = "/".join(urlsplit(url).path.split("/")[:3])
            target = urlsplit(response.url)
            requested_path = urlsplit(url).path.rstrip("/")
            same_section = target.path == prefix or target.path.startswith(prefix + "/")
            # An institution's root is valid when explicitly requested. A missing
            # subsection silently redirected to that root is not that subsection.
            root_fallback = requested_path != prefix and target.path.rstrip("/") == prefix
            if target.netloc != "www.gov.pl" or not same_section or root_fallback:
                raise FetchError("Redirect left the configured institution section")
        fingerprints.append(hashlib.sha256(response.body).hexdigest())
        if response.status == 304:
            return [], 0, response, ""
        parsed, bad = parse(source, response)
        entries.extend(parsed)
        rejected += bad
    digest = hashlib.sha256("".join(fingerprints).encode()).hexdigest()
    return entries, rejected, response, digest


def fetch_web_pages(source, client):
    if source.config.get("pagination") != "ecl_next":
        raise FetchError("Unknown HTML pagination profile")
    original = urlsplit(source.url)
    entries, rejected, fingerprints, seen, identities = [], 0, [], set(), set()
    url = source.url
    maximum = min(20, max(1, int(source.config.get("max_pages", 20))))

    def same_resource(value):
        candidate = urlsplit(value)
        return (
            candidate.scheme == "https"
            and not candidate.username
            and (candidate.netloc, candidate.path) == (original.netloc, original.path)
        )

    for _ in range(maximum):
        if url in seen or not same_resource(url):
            raise FetchError("HTML pagination loop or external resource")
        seen.add(url)
        response = client.get(url)
        if response.status != 200 or not same_resource(response.url):
            raise FetchError("HTML pagination response left configured resource")
        batch, bad = parse(source, response)
        for entry in batch:
            if entry.key in identities:
                raise FetchError("Repeated HTML record across pages; no partial batch committed")
            identities.add(entry.key)
        entries.extend(batch)
        rejected += bad
        fingerprints.append(hashlib.sha256(response.body).hexdigest())
        soup = BeautifulSoup(response.body, "html.parser")
        links = soup.select(".ecl-pagination__item--next a[href]")
        if len(links) > 1:
            raise FetchError("Ambiguous HTML next page")
        if not links:
            return (
                entries,
                rejected,
                response,
                hashlib.sha256("".join(fingerprints).encode()).hexdigest(),
            )
        url = urljoin(response.url, links[0]["href"])
    raise FetchError("HTML page budget exceeded; no partial batch committed")


def ingest_source(source, client):
    from ingestion.legislation import sync_item

    now = timezone.now()
    expected_policy = policy_snapshot(source)
    source.runs.filter(status="running").update(
        status="interrupted", finished_at=now, error="Previous process stopped before completion"
    )
    run = IngestionRun.objects.create(source=source)
    try:
        current = Source.objects.filter(pk=source.pk).first()
        check_policy(current, expected_policy)
        source = current  # Fresh validators, failures and schedule; no lock during HTTP.
        ems = source.adapter == "json" and source.config.get("mode") == "ems_initial_activations"
        if ems and source.config.get("terms_accepted") is not True:
            raise FetchError("EMS metadata terms require explicit approval before persistence")
        entries, rejected, response, digest = fetch_entries(source, client)
        run.http_status = response.status
        run.response_hash = digest
        run.fetched = len(entries)
        run.rejected = rejected
        check_policy(source, expected_policy)
        with transaction.atomic():
            current = Source.objects.select_for_update().filter(pk=source.pk).first()
            check_policy(current, expected_policy)
            source = current
            for entry in entries:
                fields = {
                    "title": entry.title,
                    "url": entry.url,
                    "summary": entry.summary,
                    "published_at": date_value(entry.published),
                    "source_updated_at": date_value(entry.updated),
                    "content_hash": entry.digest(),
                    "metadata": entry.metadata,
                    "last_seen_at": now,
                }
                item, created = Item.objects.get_or_create(
                    source=source, external_id=entry.key, defaults=fields
                )
                if not created:
                    from ingestion.detail_descriptions import preserve_description

                    preserve_description(item, fields)
                if created:
                    enrich(item)
                    classify_and_save(item)
                    suggest_duplicates(item)
                    run.created += 1
                elif item.content_hash != fields["content_hash"]:
                    if not item.revision:
                        enrich(item, origin="baseline")
                    for key, value in fields.items():
                        setattr(item, key, value)
                    item.save(update_fields=list(fields))
                    enrich(item)
                    classify_and_save(item)
                    suggest_duplicates(item)
                    run.updated += 1
                else:
                    if not item.revision:
                        enrich(item, origin="baseline")
                    Item.objects.filter(pk=item.pk).update(last_seen_at=now)
                    run.unchanged += 1
                sync_item(item)
            run.status = "partial" if rejected else "success"
            if response.status == 304:
                run.status = "not_modified"
            source.failures = 0
            source.last_success_at = now
            source.next_fetch_at = now + timedelta(minutes=max(15, source.interval_minutes))
            if source.adapter != "eli" and response.status != 304:
                source.etag = response.headers.get("etag", "")[:500]
                source.last_modified = response.headers.get("last-modified", "")[:100]
            source.save(
                update_fields=[
                    "failures",
                    "last_success_at",
                    "next_fetch_at",
                    "etag",
                    "last_modified",
                ]
            )
            run.finished_at = timezone.now()
            run.save()
    except Exception as exc:
        # One broken parser/source must not stop the rest. Do not expose remote payloads.
        run.status = "failed"
        run.created = run.updated = run.unchanged = 0
        run.error = str(exc)[:300] if isinstance(exc, FetchError) else type(exc).__name__
        if not isinstance(exc, (SourcePolicyChanged, ProviderPolicyBlocked)):
            run.http_status = getattr(exc, "status", None)
            try:
                with transaction.atomic():
                    current = Source.objects.select_for_update().filter(pk=source.pk).first()
                    check_policy(current, expected_policy)
                    current.failures += 1
                    delay = min(86400, 60 * 2 ** min(current.failures, 10))
                    delay = max(delay, getattr(exc, "retry_after", 0))
                    current.next_fetch_at = timezone.now() + timedelta(seconds=delay)
                    current.save(update_fields=["failures", "next_fetch_at"])
            except SourcePolicyChanged as changed:
                run.error = str(changed)
        run.finished_at = timezone.now()
        run.save()
        logger.warning("Source %s failed: %s", source.slug, run.error)
    return run


def run_batch(source_slug=None, force=False, dry_run=False, limit=None):
    sources = Source.objects.all()
    if source_slug:
        sources = sources.filter(slug=source_slug)
    if not dry_run:
        sources = sources.filter(enabled=True, activation_approved=True)
    if not force and not dry_run:
        sources = sources.filter(next_fetch_at__lte=timezone.now())
    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Batch limit must be a positive integer")
        sources = sources.order_by("next_fetch_at", "pk")[:limit]
    results = []
    with batch_lock() as acquired:
        if not acquired:
            return [{"status": "busy"}]
        client = Client()
        for source in sources:
            if dry_run:
                try:
                    entries, rejected, response, _ = fetch_entries(
                        source, client, conditional=False
                    )
                    from ingestion.legislation import listing_data, observations, source_identity

                    for entry in entries:
                        candidate = Item(source=source, url=entry.url, metadata=entry.metadata)
                        identity = source_identity(candidate)
                        if identity:
                            observations(listing_data(candidate, identity[2]), identity[2])
                    results.append(
                        {
                            "source": source.slug,
                            "status": "dry_run",
                            "count": len(entries),
                            "rejected": rejected,
                            "http": response.status,
                        }
                    )
                except Exception as exc:
                    results.append(
                        {
                            "source": source.slug,
                            "status": "failed",
                            "error": str(exc)
                            if isinstance(exc, FetchError)
                            else type(exc).__name__,
                        }
                    )
            else:
                run = ingest_source(source, client)
                results.append(
                    {
                        "source": source.slug,
                        "status": run.status,
                        "fetched": run.fetched,
                        "created": run.created,
                        "updated": run.updated,
                        "unchanged": run.unchanged,
                        "error": run.error,
                    }
                )
    return results
