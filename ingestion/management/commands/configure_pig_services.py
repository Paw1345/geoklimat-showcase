"""Prepare bounded metadata channels without closing full PIG gaps."""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.pig_services import NOTE, PROFILES, config, slug
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure reviewed PIG WMS metadata channels; dry-run default, no HTTP or activation."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            ids = {i for p in PROFILES.values() for i in p["registry_ids"]}
            entries = {e.registry_id: e for e in RegistryEntry.objects.filter(registry_id__in=ids)}
            if set(entries) != ids:
                raise CommandError("Import the registry first")
            # Validate all profiles before writing any; preserve administrator edits.
            existing = {}
            for key, profile in PROFILES.items():
                source = Source.objects.filter(slug=slug(key)).first()
                if source and (
                    (source.adapter, source.url, source.config)
                    != ("ogc", profile["url"], config(key))
                    or set(source.registry_entries.values_list("registry_id", flat=True))
                    != set(profile["registry_ids"])
                ):
                    raise CommandError("Preserving administrator configuration or linkage")
                if any(
                    entries[i].integration_state != "needs_connector"
                    for i in profile["registry_ids"]
                ):
                    raise CommandError("Preserving existing registry state")
                existing[key] = source
            for key, profile in PROFILES.items():
                if existing[key]:
                    self.stdout.write(f"{slug(key)}: unchanged; audit/activation preserved")
                    continue
                self.stdout.write(f"{slug(key)}: create disabled WMS metadata channel")
                if options["apply"]:
                    source = Source.objects.create(
                        slug=slug(key),
                        name=profile["name"],
                        organization="PIG-PIB",
                        adapter="ogc",
                        url=profile["url"],
                        config=config(key),
                        language="pl",
                        priority=1,
                        interval_minutes=360,
                        scope=NOTE,
                        reuse_notes=(
                            "Warunki źródłowe w AccessConstraints; "
                            "brak ustalonej zgody na redystrybucję danych."
                        ),
                        enabled=False,
                        activation_approved=False,
                    )
                    for i in profile["registry_ids"]:
                        entries[i].sources.add(source)
                        entries[i].integration_note = NOTE
                        entries[i].save(update_fields=["integration_note"])
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
