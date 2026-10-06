from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import Item, Publication
from ingestion.publications import enrich, suggest_duplicates
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Normalize stored records without HTTP. Defaults to read-only inventory."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--only-missing", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write(
            f"Items: {Item.objects.count()}; unversioned: {Item.objects.filter(revision=0).count()}"
        )
        if not options["apply"]:
            self.stdout.write("Dry run. Use --apply to normalize and create baseline versions.")
            return
        with batch_lock() as acquired:
            if not acquired:
                raise CommandError("Ingestion is busy; retry later.")
            items = Item.objects.select_related("source").order_by("pk")
            if options["only_missing"]:
                items = items.filter(revision=0)
            for item in items.iterator():
                with transaction.atomic():
                    enrich(item, origin="baseline" if not item.revision else "normalization")
            for item in (
                Item.objects.select_related("source")
                .filter(source__adapter__in=["html", "feed"])
                .iterator()
            ):
                suggest_duplicates(item)
        count = Publication.objects.filter(items__isnull=False).distinct().count()
        self.stdout.write(f"Done. Nonempty groups: {count}")
