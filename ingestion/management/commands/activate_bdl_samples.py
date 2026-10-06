import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from ingestion.bdl_probe import COLLECTIONS, LICENSE, MAX_BYTES
from ingestion.bdl_samples import capacity, run_sample, slug, validate_profile
from ingestion.management.commands.configure_stac_recent import configuration_lock
from ingestion.models import DataChannel
from ingestion.transport import Client, FetchError

STATE = ("enabled", "access_approved", "last_status", "last_error", "checked_at", "next_fetch_at")


class Command(BaseCommand):
    help = "Dry-run or scoped BDL sample activation using declared API CC BY4.0; fresh GET guards."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--wait-seconds", type=int, default=0)

    def handle(self, *args, **options):
        if not 0 <= options["wait_seconds"] <= 30:
            raise CommandError("Wait budget must be between0 and30 seconds")
        try:
            capacity(3 * MAX_BYTES * 30)
            prepared = []
            for collection in COLLECTIONS:
                channel = DataChannel.objects.select_related("registry").get(slug=slug(collection))
                validate_profile(channel)
                state = tuple(getattr(channel, key) for key in STATE)
                receipt = None
                if not (channel.enabled and channel.access_approved):
                    result = run_sample(channel, Client(), dry_run=True)
                    if (
                        result["status"] != "dry_run"
                        or result.get("declared_api_license") != LICENSE
                        or result.get("records") != 1
                        or not result.get("original_response_preserved")
                    ):
                        raise FetchError("Fresh nonempty original API sample required")
                    receipt = {"checked_at": timezone.now(), "bytes": result["bytes"], "records": 1}
                prepared.append((channel.pk, state, receipt))
            with configuration_lock(options["wait_seconds"]), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(726043)")
                capacity(3 * MAX_BYTES * 30)
                rows = []
                for pk, state, receipt in prepared:
                    channel = (
                        DataChannel.objects.select_for_update()
                        .select_related("registry")
                        .get(pk=pk)
                    )
                    validate_profile(channel)
                    if tuple(getattr(channel, key) for key in STATE) != state:
                        raise FetchError("BDL approval or schedule changed during probe")
                    active = channel.enabled and channel.access_approved
                    if not active and (
                        receipt is None
                        or timezone.now() - receipt["checked_at"] > timedelta(minutes=10)
                    ):
                        raise FetchError("Fresh BDL receipt expired")
                    rows.append((channel, receipt, active))
                for channel, receipt, active in rows:
                    if options["apply"] and not active:
                        channel.enabled = channel.access_approved = True
                        channel.checked_at = receipt["checked_at"]
                        channel.last_status, channel.last_error = "verified", ""
                        channel.next_fetch_at = timezone.now()
                        channel.save(
                            update_fields=[
                                "enabled",
                                "access_approved",
                                "checked_at",
                                "last_status",
                                "last_error",
                                "next_fetch_at",
                            ]
                        )
                self.stdout.write(
                    json.dumps(
                        {
                            "applied": options["apply"],
                            "reuse_basis": "publisher-declared-api-CC-BY4.0-original-samples",
                            "all_BDL_resources_terms_resolved": False,
                            "channels": [
                                {"slug": c.slug, "already_active": a, "receipt": p}
                                for c, p, a in rows
                            ],
                            "snapshots_written": False,
                        },
                        cls=DjangoJSONEncoder,
                        ensure_ascii=False,
                    )
                )
        except (DataChannel.DoesNotExist, FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError(
                "BDL activation refused: fresh probe, profile, capacity or drift"
            ) from exc
