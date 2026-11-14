from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ingestion.marine_audit import marine_audit_current, marine_target
from ingestion.models import Source
from ingestion.service import batch_lock
from ingestion.sopo_policy import blocked_source as sopo_blocked_source


class Command(BaseCommand):
    help = "Approve newly imported public channels with a fresh successful audit. Dry-run default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--source", action="append", help="Limit activation to selected slugs")

    def handle(self, *args, **options):
        with batch_lock() as acquired:
            if not acquired:
                self.stdout.write("Ingestion busy; activation unchanged")
                return
            sources = Source.objects.filter(
                slug__startswith="registry-",
                last_audit__status="verified",
                last_audit__rejected=0,
                last_audit__count__gt=0,
                audited_at__gte=timezone.now() - timedelta(hours=24),
            )
            if options["source"]:
                sources = sources.filter(slug__in=options["source"])
            candidates = list(sources)
            ids = [
                source.pk
                for source in candidates
                if not marine_target(source)
                if source.last_audit.get("url") == source.url
                and not sopo_blocked_source(source)
                and not (
                    source.config.get("mode") == "ems_initial_activations"
                    and source.config.get("terms_accepted") is not True
                )
            ]
            count = len(ids)
            if options["apply"]:
                count = (
                    Source.objects.filter(pk__in=ids)
                    .exclude(slug="registry-195")
                    .filter(Q(config__mode__isnull=True) | ~Q(config__mode="marine_products"))
                    .update(activation_approved=True, enabled=True)
                )
            for candidate in candidates:
                if not marine_target(candidate):
                    continue
                with transaction.atomic():
                    source = Source.objects.select_for_update().filter(pk=candidate.pk).first()
                    if source is None:
                        continue
                    audit = source.last_audit
                    if (
                        not marine_target(source)
                        or not isinstance(audit, dict)
                        or audit.get("status") != "verified"
                        or type(audit.get("rejected")) is not int
                        or audit["rejected"] != 0
                        or type(audit.get("count")) is not int
                        or audit["count"] <= 0
                        or audit.get("url") != source.url
                        or source.audited_at is None
                        or source.audited_at < timezone.now() - timedelta(hours=24)
                        or sopo_blocked_source(source)
                        or not marine_audit_current(source)
                    ):
                        continue
                    count += 1
                    if options["apply"]:
                        source.activation_approved = True
                        source.enabled = True
                        source.save(update_fields=["activation_approved", "enabled"])
            self.stdout.write(f"Eligible public channels: {count}; applied={options['apply']}")
