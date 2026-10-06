from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

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
            ids = [
                source.pk
                for source in sources
                if source.last_audit.get("url") == source.url
                and not sopo_blocked_source(source)
                and not (
                    source.config.get("mode") == "ems_initial_activations"
                    and source.config.get("terms_accepted") is not True
                )
            ]
            count = len(ids)
            if options["apply"]:
                Source.objects.filter(pk__in=ids).update(activation_approved=True, enabled=True)
            self.stdout.write(f"Eligible public channels: {count}; applied={options['apply']}")
