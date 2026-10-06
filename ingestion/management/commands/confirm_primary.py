from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import Item
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = (
        "Confirm primary source for an item's group using editorial evidence; preview by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("item", type=int)
        parser.add_argument("--note", required=True)
        parser.add_argument("--reviewer", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion is busy; retry later.")
            try:
                item = Item.objects.select_related("publication").get(pk=options["item"])
            except Item.DoesNotExist as exc:
                raise CommandError("Item not found") from exc
            if not item.publication_id or not options["note"].strip():
                raise CommandError("A normalized item and evidence note are required.")
            self.stdout.write(f"Primary: {item.title}; group: {item.publication_id}")
            if options["apply"]:
                group = item.publication
                group.primary_item = item
                group.primary_confirmed = True
                group.primary_note = f"{options['reviewer']}: {options['note']}"[:300]
                group.save()
                self.stdout.write("Confirmation saved.")
            else:
                self.stdout.write("Preview only. Add --apply to save.")
