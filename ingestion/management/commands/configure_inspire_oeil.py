from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.inspire_oeil import CONFIG, LEGACY_URL, NOTE, SLUG, URL
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare separate OEIL INSPIRE companion; dry-run/no HTTP; preserve EUR-Lex history."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--pause-blocked-primary", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=168).first()
            primary = Source.objects.filter(slug="eurlex-inspire").first()
            companion = Source.objects.filter(slug=SLUG).first()
            if (
                not entry
                or entry.integration_state != "linked"
                or not primary
                or (primary.adapter, primary.url, primary.config)
                != ("watch", LEGACY_URL, {"content": "#MainContent"})
                or list(primary.registry_entries.values_list("registry_id", flat=True)) != [168]
                or not entry.sources.filter(pk=primary.pk).exists()
                or entry.sources.exclude(slug__in=["eurlex-inspire", SLUG]).exists()
            ):
                raise CommandError("Preserving administrator registry/configuration")
            if companion and (
                (companion.adapter, companion.url, companion.config) != ("web", URL, CONFIG)
                or not entry.sources.filter(pk=companion.pk).exists()
                or list(companion.registry_entries.values_list("registry_id", flat=True)) != [168]
            ):
                raise CommandError("Preserving administrator companion configuration/linkage")
            if options["pause_blocked_primary"]:
                cutoff = timezone.now() - timedelta(minutes=15)
                if (
                    not companion
                    or not companion.enabled
                    or not companion.activation_approved
                    or companion.last_audit.get("status") != "verified"
                    or companion.last_audit.get("count") != 1
                    or companion.last_audit.get("rejected") != 0
                    or not companion.audited_at
                    or companion.audited_at < cutoff
                    or not companion.last_success_at
                    or companion.last_success_at < cutoff
                    or not companion.items.filter(
                        metadata__procedure_reference="2025/0393(COD)"
                    ).exists()
                    or primary.last_audit.get("status") != "unverified"
                    or primary.last_audit.get("error") != "Cannot confirm robots policy"
                    or not primary.audited_at
                    or primary.audited_at < cutoff
                ):
                    raise CommandError(
                        "Fresh successful companion and failed primary audits required"
                    )
                self.stdout.write(
                    "eurlex-inspire: pause only enabled flag; retain history/approval/config"
                )
                if options["apply"]:
                    primary.enabled = False
                    primary.save(update_fields=["enabled"])
            elif companion:
                self.stdout.write(SLUG + ": unchanged; audit/approval/history preserved")
            else:
                self.stdout.write(SLUG + ": create disabled separate official procedure companion")
                if options["apply"]:
                    companion = Source.objects.create(
                        slug=SLUG,
                        name="OEIL — procedura INSPIRE2025/0393(COD)",
                        organization="Parlament Europejski — OEIL",
                        adapter="web",
                        url=URL,
                        config=CONFIG,
                        language="en",
                        priority=primary.priority,
                        owner=primary.owner,
                        interval_minutes=360,
                        scope=NOTE,
                        enabled=False,
                        activation_approved=False,
                    )
                    companion.topics.set(primary.topics.all())
                    entry.sources.add(companion)
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
