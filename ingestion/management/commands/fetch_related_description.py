from django.core.management.base import BaseCommand, CommandError

from ingestion.models import Item
from ingestion.related_descriptions import fetch, save
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Preview one MKiŚ related excerpt; --apply stores evidence, never parent summary."

    def add_arguments(self, parser):
        parser.add_argument("item", type=int)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            with batch_lock() as acquired:
                if not acquired:
                    raise CommandError("Ingestion busy; retry later")
                item = Item.objects.select_related("source").get(pk=options["item"])
                evidence = fetch(item, Client())
                self.stdout.write(f"{evidence['title']}\n{evidence['url']}\n{evidence['excerpt']}")
                if options["apply"]:
                    updated = save(item, evidence)
                    self.stdout.write(
                        "Unchanged; no write."
                        if updated.content_hash == item.content_hash
                        else f"Saved revision {updated.revision}."
                    )
                else:
                    self.stdout.write("Dry run: no writes. Use --apply to save.")
        except (Item.DoesNotExist, FetchError) as exc:
            raise CommandError(str(exc)) from exc
