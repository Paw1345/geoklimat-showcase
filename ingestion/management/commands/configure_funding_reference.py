from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.funding_reference import NOTE, PROGRAMMES, profile
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare disabled LIFE/Horizon reference metadata channels; dry-run default, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=189).first()
            if not entry or entry.integration_state != "needs_connector":
                raise CommandError("Import registry first; preserve existing integration state")
            allowed = [profile(code)["slug"] for code in PROGRAMMES]
            if entry.sources.exclude(slug__in=allowed).exists():
                raise CommandError("Preserving administrator linkage")
            for code, name in PROGRAMMES.items():
                p = profile(code)
                existing = Source.objects.filter(slug=p["slug"]).first()
                if existing and (
                    (existing.adapter, existing.url, existing.config)
                    != ("json", p["url"], p["config"])
                    or not entry.sources.filter(pk=existing.pk).exists()
                ):
                    raise CommandError("Preserving administrator configuration/linkage")
                if existing:
                    self.stdout.write(p["slug"] + ": unchanged, audit/approval preserved")
                    continue
                self.stdout.write(p["slug"] + ": prepare disabled reference catalogue")
                if options["apply"]:
                    source = Source.objects.create(
                        slug=p["slug"],
                        name="F&T — dokumenty " + name,
                        organization=entry.organization[:120],
                        adapter="json",
                        url=p["url"],
                        config=p["config"],
                        language="en",
                        priority=int(entry.priority[1]),
                        interval_minutes=120,
                        scope=NOTE,
                        reuse_notes=entry.technical.get("Reuse / licence") or "",
                        enabled=False,
                        activation_approved=False,
                    )
                    entry.sources.add(source)
            if options["apply"]:
                entry.integration_note = NOTE
                entry.save(update_fields=["integration_note"])
            else:
                self.stdout.write("Dry-run; no writes or HTTP")
