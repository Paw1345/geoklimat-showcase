"""Independent resumable 24h scans, hard per-window caps and atomic cache reserve."""

from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.stac_recent import PROFILES, configuration
from ingestion.transport import FetchError

MODE = "stac_bounded"
WINDOW_BYTES = 16 * 1024**2
WINDOW_PAGES = 20
RESERVE_BYTES = 1024**3
MAX_AGE = timedelta(hours=48)


def profile(parent):
    base = configuration(parent)
    base.pop("head_max_pages")
    base.pop("head_window_hours")
    return {
        **base,
        "mode": MODE,
        "window_hours": 24,
        "window_max_bytes": WINDOW_BYTES,
        "window_max_pages": WINDOW_PAGES,
        "cache_reserve_bytes": RESERVE_BYTES,
    }


def validate_profile(channel):
    parent_id = channel.config.get("history_channel_id")
    if type(parent_id) is not int or parent_id <= 0:
        raise FetchError("Bounded STAC parent identity invalid")
    try:
        parent = DataChannel.objects.get(pk=parent_id)
    except DataChannel.DoesNotExist as exc:
        raise FetchError("Bounded STAC parent missing") from exc
    if (
        parent.slug not in PROFILES
        or channel.kind != "stac"
        or channel.url != parent.url
        or channel.region != parent.region
        or channel.registry_id != parent.registry_id
        or channel.slug != parent.slug + "-bounded"
        or channel.config != profile(parent)
        or channel.interval_minutes != 1440
    ):
        raise FetchError("Bounded STAC reviewed profile changed")
    return parent


def validate_request(channel, url):
    from ingestion.stac_recent import validate_request as validate

    return validate(channel, url, max_age=MAX_AGE)


def validate_page(channel, payload, summary):
    from ingestion.stac_recent import validate_page as validate

    validate(channel, payload, summary, max_age=MAX_AGE)


def start_url(channel):
    from ingestion.data_paging import initial_url

    parent = validate_profile(channel)
    surrogate = DataChannel(slug=parent.slug + "-recent", config=configuration(parent))
    surrogate.kind, surrogate.url = parent.kind, parent.url
    surrogate.region, surrogate.registry_id = parent.region, parent.registry_id
    return initial_url(surrogate)


def defaults(parent):
    return {
        "registry_id": parent.registry_id,
        "name": parent.name + " — ograniczony przebieg 24h",
        "kind": "stac",
        "url": parent.url,
        "region": parent.region,
        "config": profile(parent),
        "interval_minutes": 1440,
        "attribution": parent.attribution
        + " Osobne okno24h/do20stron/16MiB; rezerwa1GiB. Nie pełna doba ani dokładna maska.",
    }


def advance(channel, client):
    """Caller holds existing batch lock; never modifies history or head channels."""
    from ingestion.data_paging import digest, validate_page_url
    from ingestion.data_service import collect, store_snapshot
    from ingestion.stac_coverage import aware

    validate_profile(channel)
    if not channel.enabled or not channel.access_approved:
        raise FetchError("Bounded STAC scan requires explicit approval and activation")
    config_hash = digest([channel.url, channel.kind, channel.region, channel.config])
    harvest = channel.harvests.filter(status__in=["running", "failed", "paused"]).first()
    if harvest:
        if harvest.config_hash != config_hash:
            raise FetchError("Bounded STAC historical configuration changed; preserve cursor")
        parts = dict(parse_qsl(urlsplit(harvest.start_url).query)).get("datetime", "").split("/")
        end = aware(parts[1]) if len(parts) == 2 else None
        if end and end < timezone.now() - MAX_AGE:
            harvest.status, harvest.finished_at = "expired", timezone.now()
            harvest.save(update_fields=["status", "finished_at"])
            harvest = None
    if harvest is None:
        url = start_url(channel)
        harvest = DataHarvest.objects.create(
            channel=channel, config_hash=config_hash, start_url=url, next_url=url
        )
    try:
        for _ in range(2):
            validate_profile(channel)
            validate_request(channel, harvest.start_url)
            remaining = WINDOW_BYTES - harvest.size_bytes
            # Reserve a whole maximum-sized page before issuing another GET.
            if harvest.pages >= WINDOW_PAGES or remaining < settings.DATA_AUTO_MAX_BYTES:
                harvest.status, harvest.finished_at = "bounded", timezone.now()
                harvest.error = "Window page or byte budget reached"
                harvest.save(update_fields=["status", "finished_at", "error"])
                break
            used = DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
            if (
                used + settings.DATA_AUTO_MAX_BYTES + RESERVE_BYTES
                > settings.DATA_STORAGE_MAX_BYTES
            ):
                harvest.status, harvest.error = "paused", "Shared cache reserve reached"
                harvest.save(update_fields=["status", "error"])
                break
            validate_page_url(harvest.start_url, harvest.next_url, {"token"})
            request_hash = digest(harvest.next_url)
            if request_hash in harvest.visited_urls:
                raise FetchError("Bounded STAC pagination loop")
            client.max_bytes = settings.DATA_AUTO_MAX_BYTES
            payload, media, summary = collect(channel, client, request_url=harvest.next_url)
            validate_page_url(harvest.next_url, summary["request_url"], set())
            fingerprint = summary["page_fingerprint"]
            if summary["raw_records"] and fingerprint in harvest.page_fingerprints:
                raise FetchError("Bounded STAC repeated page")
            next_url = summary["next_url"]
            if next_url:
                validate_page_url(harvest.start_url, next_url, {"token"})
                if digest(next_url) in [*harvest.visited_urls, request_hash]:
                    raise FetchError("Bounded STAC next-page loop")
            summary.update(harvest_id=harvest.pk, page=harvest.pages + 1, partial=True)
            with transaction.atomic():
                # Approval/configuration may change while the GET is in flight.
                DataChannel.objects.select_for_update().get(pk=channel.config["history_channel_id"])
                current = DataChannel.objects.select_for_update().get(pk=channel.pk)
                validate_profile(current)
                if not current.enabled or not current.access_approved:
                    raise FetchError("Bounded STAC approval changed during request")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(726043)")
                used = DataSnapshot.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
                if used + len(payload) + RESERVE_BYTES > settings.DATA_STORAGE_MAX_BYTES:
                    harvest.status, harvest.error = "paused", "Shared cache reserve reached"
                    harvest.save(update_fields=["status", "error"])
                    break
                if len(payload) > remaining:
                    raise FetchError("Bounded STAC page exceeds window budget")
                store_snapshot(channel, payload, media, summary, deduplicate=False)
                harvest.pages += 1
                harvest.records += summary["records"]
                harvest.size_bytes += len(payload)
                harvest.next_url = next_url
                harvest.visited_urls.append(request_hash)
                harvest.page_fingerprints.append(fingerprint)
                harvest.status = "running" if next_url else "complete"
                if next_url and (
                    harvest.pages >= WINDOW_PAGES
                    or WINDOW_BYTES - harvest.size_bytes < settings.DATA_AUTO_MAX_BYTES
                ):
                    harvest.status = "bounded"
                harvest.finished_at = None if harvest.status == "running" else timezone.now()
                harvest.error = ""
                harvest.save()
            if harvest.status != "running":
                break
    except Exception as exc:
        harvest.refresh_from_db()
        harvest.status, harvest.error = "failed", type(exc).__name__
        harvest.save(update_fields=["status", "error"])
        raise
    return {
        "channel": channel.slug,
        "status": "collecting" if harvest.status == "running" else harvest.status,
        "harvest_id": harvest.pk,
        "pages": harvest.pages,
        "records": harvest.records,
        "bytes": harvest.size_bytes,
    }
