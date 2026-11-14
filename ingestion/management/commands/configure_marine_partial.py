"""Switch the disabled Marine source between reviewed profiles; dry-run by default."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL
from ingestion.marine_history import PROFILES, MarineProfileState, profile_name
from ingestion.models import Source
from ingestion.service import batch_lock
from ingestion.worker_presence import workers_stopped


class Command(BaseCommand):
    help = (
        "Preview or apply a Marine profile change. --apply requires a free batch lock and "
        "proof that no ingestion worker is running, and raises the profile generation so "
        "every earlier audit stops qualifying for activation."
    )

    def add_arguments(self, parser):
        parser.add_argument("--profile", choices=sorted(PROFILES), default="partial")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        target = options["profile"]
        with batch_lock() as acquired, workers_stopped() as idle, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; profile unchanged")
            source = Source.objects.select_for_update().filter(slug="registry-195").first()
            if source is None or source.adapter != "stac" or source.url != URL:
                raise CommandError("Reviewed Marine source not found")
            if source.enabled or source.activation_approved:
                raise CommandError("Source must already be disabled and unapproved")
            if source.config not in (CONFIG, PARTIAL_CONFIG):
                raise CommandError("Preserving unreviewed administrator configuration")
            links = list(
                source.registry_entries.select_for_update()
                .order_by("registry_id")
                .values_list("registry_id", flat=True)[:2]
            )
            if links != [195] or source.runs.filter(status="running").exists():
                raise CommandError("Registry linkage or running import prevents profile change")
            state = MarineProfileState.objects.select_for_update().filter(source=source).first()
            generation = state.generation if state is not None else 0
            summary = (
                f"profile {profile_name(source)} -> {target}; "
                f"generation {generation} -> {generation + 1}; earlier audits stop qualifying"
            )
            if not options["apply"]:
                worker = "no worker running" if idle else "worker running, apply would refuse"
                self.stdout.write(f"Dry-run: {summary}; {worker}; configuration unchanged")
                return
            if not idle:
                raise CommandError(
                    "Worker running or another profile change in progress; "
                    "stop the worker first; profile unchanged"
                )
            if state is None:
                state = MarineProfileState.objects.create(source=source)
            source.config = PROFILES[target].copy()
            source.save(update_fields=["config"])
            state.profile = target
            state.generation += 1
            state.save()
            self.stdout.write(f"Applied: {summary}; source stays disabled and unapproved")
