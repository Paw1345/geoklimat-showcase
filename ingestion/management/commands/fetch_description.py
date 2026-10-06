from django.core.management.base import BaseCommand, CommandError

from ingestion.detail_descriptions import eligible, extract, refreshable, save_description
from ingestion.models import Item
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "One GUGiK/GIOS/INSPIRE excerpt; GET preview by default, --apply saves a revision."

    def add_arguments(self, parser):
        parser.add_argument("item", type=int)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--refresh", action="store_true", help="Recheck an existing excerpt.")

    def handle(self, *args, **options):
        try:
            with batch_lock() as acquired:
                if not acquired:
                    raise CommandError("Ingestion busy; retry later")
                item = Item.objects.select_related("source").get(pk=options["item"])
                if not eligible(item):
                    raise CommandError("No approved detail profile for this item")
                if options["refresh"] and not refreshable(item):
                    raise CommandError("Refresh requires an existing verified detail excerpt")
                if item.has_description and not options["refresh"]:
                    self.stdout.write("Description already present; no fetch or write.")
                    return
                summary, digest = extract(item, Client().get(item.url))
                self.stdout.write(summary)
                if options["apply"]:
                    updated = save_description(item, summary, digest, refresh=options["refresh"])
                    if updated.content_hash == item.content_hash:
                        self.stdout.write("Unchanged source text; no write or new revision.")
                    else:
                        self.stdout.write(f"Saved item {item.pk}, revision {updated.revision}.")
                else:
                    self.stdout.write("Dry run: no writes. Use --apply to save.")
        except (Item.DoesNotExist, FetchError) as exc:
            raise CommandError(str(exc)) from exc
