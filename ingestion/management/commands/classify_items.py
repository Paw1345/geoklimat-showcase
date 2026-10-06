from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from ingestion.classification import classify, classify_and_save
from ingestion.models import Item
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Classify existing metadata locally; dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--item", type=int, action="append", help="Limit to these item IDs.")

    def handle(self, *args, **options):
        counts = Counter()
        changed = 0
        with batch_lock() as acquired:
            if not acquired:
                raise CommandError("Ingestion is busy; retry later.")
            items = Item.objects.select_related("source").order_by("pk")
            if options["item"]:
                items = items.filter(pk__in=options["item"])
                if items.count() != len(set(options["item"])):
                    raise CommandError("One or more item IDs do not exist; no changes made.")
            for item in items.iterator():
                counts[classify(item)["auto_state"]] += 1
                if options["apply"]:
                    changed += classify_and_save(item)
        self.stdout.write(f"Automatic results: {dict(counts)}; changed: {changed}")
        if not options["apply"]:
            self.stdout.write("Dry run: no data changed. Use --apply to save.")
