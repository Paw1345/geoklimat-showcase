"""Approve manual access only; never enable the scheduler or reset a harvest."""

import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from ingestion.bdl_probe import LICENSE
from ingestion.bdl_samples import capacity
from ingestion.bdl_scan import MANUAL_MAX_BYTES, SLUG, advance, validate_profile
from ingestion.management.commands.configure_stac_recent import configuration_lock
from ingestion.models import DataChannel
from ingestion.transport import Client, FetchError

STATE = ("enabled", "access_approved", "last_status", "last_error", "checked_at", "next_fetch_at")


class Command(BaseCommand):
    help = "Dry-run or guarded manual RDLP access approval, scheduler stays disabled."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--wait-seconds", type=int, default=0)

    def handle(self, *args, **options):
        if not 0 <= options["wait_seconds"] <= 30:
            raise CommandError("Wait budget must be between 0 and 30 seconds")
        try:
            channel = DataChannel.objects.select_related("registry").get(slug=SLUG)
            validate_profile(channel)
            baseline = tuple(getattr(channel, key) for key in STATE)
            history = (channel.harvests.count(), channel.snapshots.count())
            receipt = None
            if not channel.access_approved:
                if any(history):
                    raise FetchError("Unapproved profile with history requires separate review")
                capacity(MANUAL_MAX_BYTES)
                result = advance(channel, Client(), dry_run=True)
                if (
                    result.get("status") != "dry_run"
                    or result.get("records") != 0
                    or type(result.get("source_count")) is not int
                    or not 1 <= result["source_count"] <= 100
                    or result.get("declared_api_license") != LICENSE
                    or result.get("original_response_preserved") is not True
                    or not isinstance(result.get("identity_scope_hash"), str)
                    or len(result["identity_scope_hash"]) != 64
                    or type(result.get("bytes")) is not int
                    or not 0 < result["bytes"] <= 5 * 1024 * 1024
                ):
                    raise FetchError(
                        "Fresh nonempty RDLP manifest and raw first-page probe required"
                    )
                receipt = {"checked_at": timezone.now(), "result": result}
                capacity(result["source_count"] * MANUAL_MAX_BYTES)
            with configuration_lock(options["wait_seconds"]), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(726043)")
                current = (
                    DataChannel.objects.select_for_update()
                    .select_related("registry")
                    .get(pk=channel.pk)
                )
                validate_profile(current)
                if (
                    tuple(getattr(current, key) for key in STATE) != baseline
                    or (current.harvests.count(), current.snapshots.count()) != history
                ):
                    raise FetchError(
                        "RDLP profile, approval, schedule or history changed during probe"
                    )
                if not current.access_approved:
                    if receipt is None or timezone.now() - receipt["checked_at"] > timedelta(
                        minutes=10
                    ):
                        raise FetchError("RDLP receipt expired")
                    capacity(receipt["result"]["source_count"] * MANUAL_MAX_BYTES)
                    if options["apply"]:
                        current.access_approved = True
                        current.last_status, current.last_error = "verified", ""
                        current.checked_at = receipt["checked_at"]
                        current.save(
                            update_fields=[
                                "access_approved",
                                "last_status",
                                "last_error",
                                "checked_at",
                            ]
                        )
                self.stdout.write(
                    json.dumps(
                        {
                            "applied": options["apply"],
                            "slug": current.slug,
                            "already_approved": baseline[1],
                            "automatic_collection_enabled": False,
                            "snapshots_written": False,
                            "harvests_reset": False,
                            "source_count": receipt["result"]["source_count"] if receipt else None,
                            "reuse_basis": "publisher-declared-API-CC-BY4.0-original-RDLP",
                            "all_BDL_resources_terms_resolved": False,
                        }
                    )
                )
        except (DataChannel.DoesNotExist, FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError(
                "RDLP approval refused: profile, fresh probe, budget or drift"
            ) from exc
