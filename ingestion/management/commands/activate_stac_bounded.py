import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from ingestion.data_paging import validate_page_url
from ingestion.data_service import collect
from ingestion.management.commands.configure_stac_recent import configuration_lock
from ingestion.models import DataChannel
from ingestion.stac_bounded import defaults, start_url
from ingestion.stac_capacity import capacity
from ingestion.stac_recent import PROFILES, validate_request
from ingestion.transport import Client, FetchError


def probe(channel):
    client = Client()
    client.max_bytes = settings.DATA_AUTO_MAX_BYTES
    start = start_url(channel)
    url, pages, records, total = start, 0, 0, 0
    visited, fingerprints = set(), set()
    for _ in range(2):
        validate_request(channel, url)  # First activation must use a fresh window.
        validate_page_url(start, url, {"token"})
        if url in visited:
            raise FetchError("Bounded STAC audit page loop")
        visited.add(url)
        payload, _, summary = collect(channel, client, request_url=url)
        validate_request(channel, summary["request_url"])
        validate_page_url(url, summary["request_url"], set())
        if summary["raw_records"] and summary["page_fingerprint"] in fingerprints:
            raise FetchError("Bounded STAC audit repeated page")
        fingerprints.add(summary["page_fingerprint"])
        pages += 1
        records += summary["records"]
        total += len(payload)
        url = summary["next_url"]
        if not url:
            break
        validate_page_url(start, url, {"token"})
        validate_request(channel, url)
        if url in visited:
            raise FetchError("Bounded STAC audit next-page loop")
    return {
        "checked_at": timezone.now(),
        "pages": pages,
        "records": records,
        "bytes": total,
        "more_available": bool(url),
        "stored": False,
        "full_day_coverage_verified": False,
    }


class Command(BaseCommand):
    help = "Dry-run bounded STAC activation after fresh GET and atomic profile/cache guards."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--wait-seconds", type=int, default=0)

    def handle(self, *args, **options):
        if not 0 <= options["wait_seconds"] <= 30:
            raise CommandError("Wait budget must be between 0 and 30 seconds")
        try:
            prepared = []
            for slug in PROFILES:
                parent = DataChannel.objects.get(slug=slug)
                expected = defaults(parent)
                child = DataChannel.objects.get(slug=slug + "-bounded")
                if any(getattr(child, key) != value for key, value in expected.items()):
                    raise FetchError("Modified bounded profile; preserve administrator edits")
                active = child.enabled and child.access_approved
                state = (
                    child.enabled,
                    child.access_approved,
                    child.next_fetch_at,
                    child.checked_at,
                    child.last_status,
                    child.last_error,
                )
                prepared.append(
                    (parent.pk, child.pk, expected, state, None if active else probe(child))
                )
            with configuration_lock(options["wait_seconds"]), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(726043)")
                rows = []
                needs_activation = any(item[4] is not None for item in prepared)
                budget = capacity(timezone.now(), daily_budget_mib=32, reserve_mib=1024)
                if needs_activation and budget["fits_with_sample_estimate"] is not True:
                    raise FetchError("Cache scenario or recent sample estimate unavailable")
                for parent_id, child_id, expected, state, outcome in prepared:
                    parent = DataChannel.objects.select_for_update().get(pk=parent_id)
                    child = DataChannel.objects.select_for_update().get(pk=child_id)
                    if defaults(parent) != expected or any(
                        getattr(child, key) != value for key, value in expected.items()
                    ):
                        raise FetchError("Bounded STAC profile changed during audit")
                    if outcome is not None:
                        current = (
                            child.enabled,
                            child.access_approved,
                            child.next_fetch_at,
                            child.checked_at,
                            child.last_status,
                            child.last_error,
                        )
                        if current != state or not timedelta(0) <= timezone.now() - outcome[
                            "checked_at"
                        ] <= timedelta(minutes=10):
                            raise FetchError("Bounded STAC approval changed or audit expired")
                        if options["apply"]:
                            child.enabled = child.access_approved = True
                            child.last_status, child.last_error = "verified", ""
                            child.checked_at = outcome["checked_at"]
                            child.next_fetch_at = timezone.now()
                            child.save(
                                update_fields=[
                                    "enabled",
                                    "access_approved",
                                    "last_status",
                                    "last_error",
                                    "checked_at",
                                    "next_fetch_at",
                                ]
                            )
                    elif not (child.enabled and child.access_approved):
                        raise FetchError("Bounded STAC approval changed during activation")
                    rows.append(
                        {
                            "channel_id": child.pk,
                            "apply": options["apply"],
                            "audit": outcome,
                            "history_modified": False,
                        }
                    )
            self.stdout.write(
                json.dumps(
                    {"rows": rows, "fits_cache_scenario": budget["fits_with_sample_estimate"]},
                    cls=DjangoJSONEncoder,
                )
            )
        except (
            DataChannel.DoesNotExist,
            FetchError,
            ValueError,
            TypeError,
            AttributeError,
            KeyError,
            RecursionError,
        ) as exc:
            raise CommandError(
                "Bounded STAC activation refused: audit, profile, approval or cache"
            ) from exc
