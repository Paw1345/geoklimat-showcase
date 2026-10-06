from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from ingestion.legislation import (
    fetch_details,
    listing_data,
    observations,
    source_identity,
    sync_item,
)
from ingestion.models import Item
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Index explicit legislative metadata. Dry-run default; --details GET for one item only."

    def add_arguments(self, parser):
        parser.add_argument("--item", type=int)
        parser.add_argument("--details", action="store_true")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        if options["details"] and not options["item"]:
            raise CommandError("--details requires exactly one --item; no unbounded HTTP backfill")
        items = Item.objects.select_related("source").filter(
            Q(source__adapter="eli") | Q(source__config__mode="sejm")
        )
        if options["item"]:
            items = items.filter(pk=options["item"])
            if not items.exists():
                raise CommandError("No reviewed legislative source for this item")
        count, event_count = 0, 0
        with batch_lock() as acquired:
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            client = Client()
            for item in items.iterator(chunk_size=200):
                identity = source_identity(item)
                if identity is None:
                    continue
                try:
                    data = (
                        fetch_details(item, client)
                        if options["details"]
                        else listing_data(item, identity[2])
                    )
                    events, _ = observations(data, identity[2])
                    if options["apply"]:
                        sync_item(item, data if options["details"] else None)
                except (FetchError, ValueError) as exc:
                    raise CommandError(f"Item {item.pk}: {exc}") from exc
                count += 1
                event_count += len(events)
        self.stdout.write(
            f"Documents={count}; dated observations={event_count}; applied={options['apply']}"
        )
