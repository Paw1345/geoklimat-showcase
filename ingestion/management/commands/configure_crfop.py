from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.crfop import ATTRIBUTION, REGISTERS, REGISTRY_ID, profiles
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure disabled CRFOP register tables (registry 15). Dry-run default, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=REGISTRY_ID).first()
            if not entry:
                raise CommandError("Import the registry first")
            # Validate the whole package before the first write.
            plan = []
            for expected in profiles():
                existing = DataChannel.objects.filter(slug=expected["slug"]).first()
                if existing and (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                    existing.attribution,
                ) != (entry.pk, "table", expected["url"], expected["config"], "PL", ATTRIBUTION):
                    raise CommandError("Preserving administrator configuration or linkage")
                plan.append((expected, existing))
            for expected, existing in plan:
                if existing:
                    self.stdout.write(f"{expected['slug']}: unchanged; audit and activation kept")
                    continue
                self.stdout.write(
                    f"{expected['slug']}: create disabled; applied={options['apply']}"
                )
                if options["apply"]:
                    register = expected["config"]["register"]
                    part = expected["config"].get("part")
                    label = REGISTERS[register]["title"].split(" - ", 1)[1]
                    DataChannel.objects.create(
                        registry=entry,
                        slug=expected["slug"],
                        name=f"CRFOP — {label}" + (f" (część {part})" if part else ""),
                        kind="table",
                        url=expected["url"],
                        config=expected["config"],
                        region="PL",
                        attribution=ATTRIBUTION,
                        # Register snapshot published irregularly; daily checks are enough.
                        interval_minutes=1440,
                    )
            # Registry state stays unchanged: SDI geometry (registry 16) is still blocked.
