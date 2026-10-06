"""Split only unconfirmed PIG service/layer groups, retaining records and history."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import Item, Publication, Source
from ingestion.pig_services import PROFILES, config, slug
from ingestion.publications import choose_primary
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Separate PIG WMS layers sharing an endpoint; dry-run default, no HTTP or deletions."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            source_ids = []
            for key, profile in PROFILES.items():
                source = Source.objects.filter(slug=slug(key)).first()
                if not source or (source.adapter, source.url, source.config) != (
                    "ogc",
                    profile["url"],
                    config(key),
                ):
                    raise CommandError("Missing or administrator-modified PIG source")
                source_ids.append(source.pk)
            items = Item.objects.filter(source_id__in=source_ids)
            if items.exclude(metadata__record_kind="service_capabilities").exists():
                raise CommandError("Unexpected PIG record kind")
            groups = list(
                Publication.objects.select_for_update().filter(
                    pk__in=items.values_list("publication_id", flat=True)
                )
            )
            moves = []
            for group in groups:
                rows = list(group.items.select_for_update().order_by("pk"))
                if len(rows) < 2:
                    continue
                if group.primary_confirmed or any(row.source_id not in source_ids for row in rows):
                    raise CommandError("Preserving confirmed or mixed editorial groups")
                if group.primary_note not in (
                    "",
                    "Reprezentant według priorytetu kanału i czasu wykrycia; "
                    "pierwotność niepotwierdzona.",
                ):
                    raise CommandError("Preserving administrator attribution note")
                keeper = group.primary_item_id or rows[0].pk
                if keeper not in {row.pk for row in rows}:
                    raise CommandError("Preserving inconsistent editorial group")
                moves.append((group, [row for row in rows if row.pk != keeper]))
            count = sum(len(rows) for _, rows in moves)
            self.stdout.write(f"PIG records to separate: {count}; applied={options['apply']}")
            if not options["apply"]:
                return
            for group, rows in moves:
                for item in rows:
                    new_group = Publication.objects.create()
                    item.publication = new_group
                    item.save(update_fields=["publication"])
                    choose_primary(new_group)
                choose_primary(group)
