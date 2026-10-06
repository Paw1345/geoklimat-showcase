import hashlib
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import Item, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Preserve existing Ekoportal history while separating dated category-page notices."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            count = 0
            for item in Item.objects.filter(source__slug="registry-017"):
                if urlsplit(item.url).path.startswith("/aktualnosci/szczegoly/"):
                    continue
                published = item.metadata.get("publication_date_raw")
                if not published:
                    raise CommandError("Existing notice date missing; manual review required")
                old_key = hashlib.sha256(item.url.encode()).hexdigest()
                new_key = hashlib.sha256((item.url + "\n" + published).encode()).hexdigest()
                if item.external_id == new_key:
                    continue
                if (
                    item.external_id != old_key
                    or Item.objects.filter(source=item.source, external_id=new_key).exists()
                ):
                    raise CommandError("Unexpected identity or collision; no changes committed")
                count += 1
                if options["apply"]:
                    item.external_id = new_key
                    item.save(update_fields=["external_id"])
            if options["apply"] and count:
                Source.objects.filter(slug="registry-017").update(
                    enabled=False,
                    activation_approved=False,
                    last_audit={},
                    audited_at=None,
                    etag="",
                    last_modified="",
                )
            self.stdout.write(
                f"Identities to repair: {count}; applied={options['apply']}; history preserved"
            )
