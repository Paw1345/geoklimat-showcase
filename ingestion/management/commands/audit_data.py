import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ingestion.data_service import run_data
from ingestion.models import DataChannel


class Command(BaseCommand):
    help = "Audit data profiles; optional activation only after a successful bounded dry-run."

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--enable", action="store_true")

    def handle(self, *args, **options):
        channel = DataChannel.objects.filter(slug=options["source"]).first()
        if not channel or channel.kind == "file":
            raise CommandError("Select an existing automatic data channel")
        if channel.kind == "stac":
            from ingestion.data_paging import RETENTION_REVIEW, digest

            profile_hash = digest([channel.url, channel.kind, channel.region, channel.config])
            if channel.harvests.filter(status=RETENTION_REVIEW, config_hash=profile_hash).exists():
                raise CommandError(
                    "Pinned STAC scan requires retention/window review; "
                    "a first-page audit cannot resume or activate it"
                )
        if channel.config.get("mode") == "bdl_rdlp_manual_scan":
            raise CommandError("Use bdl_scan; manual scan cannot be activated here")
        if channel.config.get("mode") == "bdl_raw_sample" and options["enable"]:
            raise CommandError("Use activate_bdl_samples for guarded BDL activation")
        results = run_data(channel.slug, dry_run=True)
        self.stdout.write(json.dumps(results, ensure_ascii=False))
        result = results[0]
        if result["status"] == "busy":
            raise CommandError("Collector busy; audit and activation unchanged")
        success = result["status"] == "dry_run"
        channel.last_status = "verified" if success else result["status"]
        channel.last_error = result.get("error", "")
        channel.checked_at = timezone.now()
        if options["enable"]:
            channel.access_approved = success
            channel.enabled = success
        channel.save()
