import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.jrc_files import audit_file
from ingestion.models import DataChannel
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Audit one JRC file channel (1 KiB Range); dry-run default; never enables or queues."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        channel = DataChannel.objects.filter(slug=options["source"]).first()
        if not channel:
            raise CommandError("Select an existing JRC file channel")
        try:
            result = audit_file(Client(), channel)
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            raise CommandError("JRC file audit failed; channel unchanged: " + str(exc)) from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        if not options["apply"]:
            self.stdout.write("Dry-run: no database writes, download jobs or full file fetch")
            return
        with transaction.atomic():
            locked = DataChannel.objects.select_for_update().get(pk=channel.pk)
            if (locked.url, locked.config) != (channel.url, channel.config):
                raise CommandError("Channel changed during audit; preserving configuration")
            locked.last_status = "verified"
            locked.last_error = ""
            locked.checked_at = timezone.now()
            locked.save(update_fields=["last_status", "last_error", "checked_at"])
        self.stdout.write("Recorded verified access; enabled/approval and jobs unchanged")
