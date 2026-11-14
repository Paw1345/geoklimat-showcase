"""Pinned annual PRTR releases, with a post-HTTP operator-state fence."""

import hashlib
import json
from copy import deepcopy
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from ingestion import gios_prtr_2016
from ingestion import gios_prtr_file as original
from ingestion.models import DataChannel
from ingestion.prtr_releases import parse_releases
from ingestion.transport import FetchError

SLUG = "gios-prtr-releases-2017"
URL = original.URL
CONFIG = {"mode": "gios_prtr_releases", "schema": "gios-prtr-releases-v1", "year": 2017}
ATTRIBUTION = (
    "GIOŚ / dane.gov.pl, dataset 425, resource 19734, CC BY 4.0. "
    "Przetworzono historyczny XML E-PRTR: Polska, uwolnienia za 2017 rok z nagłówka raportu. "
    "KGM = kg/rok; tekst liczb i braki zachowane, ilości przypadkowych nie dodano do całkowitych. "
    "Poufne/chronione rekordy wykluczone. Kody substancji bez słownika nazw; "
    "bez transferów, odpadów, geometrii i pełnej walidacji XSD. Nie nowe API GIOŚ."
)
SLUG_2016 = "gios-prtr-releases-2016"
CONFIG_2016 = {"mode": "gios_prtr_releases", "schema": "gios-prtr-releases-v1", "year": 2016}
ATTRIBUTION_2016 = (
    "GIOŚ / dane.gov.pl, dataset 425, resource 19735, CC BY 4.0. "
    "Przetworzono historyczny XML E-PRTR: Polska, uwolnienia za 2016 rok z nagłówka raportu. "
    "KGM = kg/rok; tekst liczb i braki zachowane, ilości przypadkowych nie dodano do całkowitych. "
    "Poufne/chronione rekordy wykluczone. Kody substancji bez słownika nazw; "
    "bez transferów, odpadów, geometrii i pełnej walidacji XSD. Nie nowe API GIOŚ."
)
STATE_FIELDS = (
    "slug",
    "name",
    "registry_id",
    "kind",
    "url",
    "config",
    "region",
    "attribution",
    "enabled",
    "access_approved",
    "interval_minutes",
    "next_fetch_at",
    "last_status",
    "last_error",
    "checked_at",
)


def state(channel):
    return deepcopy({field: getattr(channel, field) for field in STATE_FIELDS})


def profiles():
    """Reviewed annual profiles; sources are read at call time so tests can pin them."""
    return {
        2017: (SLUG, original.URL, CONFIG, ATTRIBUTION, original),
        2016: (SLUG_2016, gios_prtr_2016.URL, CONFIG_2016, ATTRIBUTION_2016, gios_prtr_2016),
    }


def profile_for(channel):
    year = channel.config.get("year") if isinstance(channel.config, dict) else None
    reviewed = profiles().get(year) if isinstance(year, int) else None
    if reviewed is None:
        raise FetchError("PRTR annual table requires the exact reviewed profile")
    return reviewed


def validate_profile(channel, approved=False):
    slug, url, config, attribution, source = profile_for(channel)
    if (
        channel.slug,
        channel.kind,
        channel.url,
        channel.config,
        channel.region,
        channel.attribution,
        channel.registry.registry_id,
    ) != (slug, "table", url, config, "PL", attribution, 49):
        raise FetchError("PRTR annual table requires the exact reviewed profile")
    if len(source.SHA256) != 64:
        raise FetchError("PRTR original checksum is not reviewed yet")
    if approved and not (channel.enabled and channel.access_approved):
        raise FetchError("PRTR annual table approval revoked or disabled")


def collect(channel, client):
    validate_profile(channel)
    _, url, config, attribution, source = profile_for(channel)
    previous_cap = client.max_bytes
    try:
        client.max_bytes = min(previous_cap, settings.DATA_AUTO_MAX_BYTES, 512 * 1024)
        source.audit_file(client)
        client.max_bytes = min(previous_cap, settings.DATA_AUTO_MAX_BYTES, source.SIZE)
        response = client.get(url)
        body = response.body
        if (
            response.status != 200
            or response.url != url
            or len(body) != source.SIZE
            or len(body) > client.max_bytes
            or hashlib.sha256(body).hexdigest() != source.SHA256
        ):
            raise FetchError("PRTR original XML changed; size, URL, status or SHA mismatch")
        rows, parsed = parse_releases(body, config["year"])
        summary = {
            **parsed,
            "product_scope": parsed["scope"],
            "scope": "PL",
            "kind": "gios_prtr_releases",
            "collector": "gios_prtr_releases",
            "partial": True,
            "source_url": url,
            "schema": config["schema"],
        }
        payload = json.dumps(
            {"records": rows, "provenance": summary, "attribution": attribution},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
        if len(payload) > min(previous_cap, settings.DATA_AUTO_MAX_BYTES):
            raise FetchError("PRTR JSON exceeds automatic payload budget")
        return payload, "application/json", summary
    finally:
        client.max_bytes = previous_cap


def locked_current(channel, expected):
    current = DataChannel.objects.select_for_update().select_related("registry").get(pk=channel.pk)
    if state(current) != expected:
        raise FetchError("PRTR operator state changed during GET; preserving it")
    validate_profile(current)
    return current


def run_profile(channel, client, dry_run=False):
    from ingestion.data_service import store_snapshot

    validate_profile(channel, approved=not dry_run)
    expected = state(channel)
    payload, media, summary = collect(channel, client)
    result = {"channel": channel.slug, "status": "dry_run", "bytes": len(payload), **summary}
    if dry_run:
        return result
    # No row or cache lock is held during HTTP. Match the canonical cache lock order.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(726043)")
        current = locked_current(channel, expected)
        validate_profile(current, approved=True)
        snapshot, created = store_snapshot(current, payload, media, summary)
        result.update(status="stored" if created else "unchanged", snapshot=snapshot.pk)
        current.last_status, current.last_error = result["status"], ""
        current.checked_at = timezone.now()
        current.next_fetch_at = current.checked_at + timedelta(
            minutes=max(15, current.interval_minutes)
        )
        current.save(update_fields=["last_status", "last_error", "checked_at", "next_fetch_at"])
    return result


def mark_failure(channel, error):
    now = timezone.now()
    # The WHERE fence also protects operator diagnostics/schedule after a failed GET.
    DataChannel.objects.filter(pk=channel.pk, **state(channel)).update(
        last_status="failed",
        last_error=error,
        checked_at=now,
        next_fetch_at=now + timedelta(minutes=max(15, channel.interval_minutes)),
    )


def apply_audit(channel, expected, result, enable):
    with transaction.atomic():
        current = locked_current(channel, expected)
        success = result["status"] == "dry_run"
        current.last_status = "verified" if success else result["status"]
        current.last_error = result.get("error", "")
        current.checked_at = timezone.now()
        fields = ["last_status", "last_error", "checked_at"]
        if enable:
            current.access_approved = current.enabled = success
            fields.extend(["access_approved", "enabled"])
        current.save(update_fields=fields)
