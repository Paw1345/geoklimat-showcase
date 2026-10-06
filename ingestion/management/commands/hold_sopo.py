"""Revoke only local SOPO scheduling permission; preserve all records and evidence."""

from django.core.management.base import BaseCommand
from django.db import transaction

from ingestion.models import Source
from ingestion.sopo_policy import blocked_source


class Command(BaseCommand):
    help = "Hold SOPO automated sources only. Dry-run default, no HTTP or data deletion."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        # Permission revocation uses the same Source row lock as import commit,
        # not the ingestion advisory lock: no harvesting/cursor/cache mutation.
        ids = [source.pk for source in Source.objects.all() if blocked_source(source)]
        with transaction.atomic():
            for source in Source.objects.select_for_update().filter(pk__in=ids).order_by("pk"):
                if not blocked_source(source):
                    continue
                changed = source.enabled or source.activation_approved
                self.stdout.write(
                    f"SOPO source {source.pk}: hold; changed={changed}; applied={options['apply']}"
                )
                if options["apply"] and changed:
                    source.enabled = source.activation_approved = False
                    source.save(update_fields=["enabled", "activation_approved"])
