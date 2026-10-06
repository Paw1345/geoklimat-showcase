from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.ems_catalogue import SLUG, TERMS, URL, validate_profile
from ingestion.models import Source
from ingestion.service import batch_lock
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = "Record explicit operator EMS terms decision; dry-run default, never enables a source."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--accept-terms", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            source = Source.objects.select_for_update().filter(slug=SLUG).first()
            if not source:
                raise CommandError("EMS source not prepared")
            try:
                validate_profile(source)
            except FetchError as exc:
                raise CommandError("Preserving administrator profile") from exc
            if set(source.registry_entries.values_list("registry_id", flat=True)) != {196}:
                raise CommandError("EMS registry linkage changed")
            if source.config["terms_accepted"]:
                self.stdout.write("EMS terms decision already recorded; no changes or activation")
                return
            if source.enabled or source.activation_approved:
                raise CommandError("EMS approval requires disabled/unapproved source")
            audit = source.last_audit
            now = timezone.now()
            if (
                not source.audited_at
                or not timedelta(0) <= now - source.audited_at <= timedelta(minutes=15)
                or audit.get("status") != "verified"
                or audit.get("url") != URL
                or audit.get("http") != 200
                or audit.get("rejected") != 0
                or type(audit.get("count")) is not int
                or not 1 <= audit["count"] <= 20
                or audit.get("robots") != "allowed-or-404"
            ):
                raise CommandError("EMS explicit approval requires fresh nonempty verified audit")
            self.stdout.write("EMS metadata-only terms decision: " + TERMS)
            if options["apply"]:
                if not options["accept_terms"]:
                    raise CommandError("Use --accept-terms only after explicit operator decision")
                source.config = {**source.config, "terms_accepted": True}
                source.reuse_notes = (
                    "Decyzja operatora: warunki CEMS dla metadanych, atrybucja; " + TERMS
                )
                source.save(update_fields=["config", "reuse_notes"])
            else:
                self.stdout.write("Dry-run; terms not accepted, no writes or activation")
