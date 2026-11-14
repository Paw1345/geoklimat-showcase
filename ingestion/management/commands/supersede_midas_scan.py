"""Operator closure of one interrupted MIDAS scan whose provider manifest changed."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.models import DataChannel, DataHarvest
from ingestion.pig_paging import manifest, validate_profile
from ingestion.transport import Client, FetchError

INTERRUPTED = ["running", "failed"]


class Command(BaseCommand):
    help = (
        "Mark one interrupted MIDAS scan superseded after a provider manifest change; "
        "dry-run default, keeps pages/snapshots/cursor, deletes nothing."
    )

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        channel = DataChannel.objects.filter(slug=options["source"]).first()
        if not channel or channel.config.get("mode") != "pig_midas_scan":
            raise CommandError("Select an existing MIDAS scan channel")
        try:
            validate_profile(channel)
        except FetchError as exc:
            raise CommandError("Preserving administrator profile") from exc
        harvest = channel.harvests.filter(status__in=INTERRUPTED).first()
        superseded = channel.harvests.filter(status="superseded").exists()
        if not harvest:
            if superseded:
                self.stdout.write("Interrupted scan already superseded; no changes")
                return
            raise CommandError("No interrupted scan; complete scans are never superseded")
        try:
            ids, scope_hash = manifest(channel, Client())
        except FetchError as exc:
            raise CommandError("Current MIDAS manifest unavailable: " + str(exc)) from exc
        if scope_hash == harvest.config_hash:
            if superseded:
                self.stdout.write("Current scan already uses the new manifest; no changes")
                return
            raise CommandError("Manifest unchanged; the scan resumes and is not superseded")
        self.stdout.write(
            f"Harvest {harvest.pk} ({harvest.status}): {harvest.pages} pages, "
            f"{harvest.records} records kept; current manifest {len(ids)} IDs"
        )
        if not options["apply"]:
            self.stdout.write("Dry-run; no writes")
            return
        now = timezone.now()
        with transaction.atomic():
            locked = DataChannel.objects.select_for_update().get(pk=channel.pk)
            row = DataHarvest.objects.select_for_update().get(pk=harvest.pk)
            if locked.next_fetch_at <= now:
                # A due channel may be inside the worker's advance(); never race its save.
                raise CommandError("Channel due for collection; retry after the worker cycle")
            if (
                row.status not in INTERRUPTED
                or row.config_hash != harvest.config_hash
                or (row.pages, row.records, row.next_url)
                != (harvest.pages, harvest.records, harvest.next_url)
            ):
                raise CommandError("Harvest changed during review; nothing written")
            row.status, row.finished_at = "superseded", now
            row.error = f"Superseded by operator: provider manifest changed ({len(ids)} IDs)"
            row.save(update_fields=["status", "finished_at", "error"])
            locked.next_fetch_at = now
            locked.save(update_fields=["next_fetch_at"])
        self.stdout.write(f"Harvest {row.pk} superseded; worker starts a new scan")
