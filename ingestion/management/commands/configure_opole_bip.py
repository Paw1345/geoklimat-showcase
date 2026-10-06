from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.opole_bip import NOTE, PROFILES, profile
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Prepare two disabled Opole BIP feeds; dry-run default/no HTTP/full gap preserved."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=132).first()
            if not entry or entry.integration_state != "needs_endpoint":
                raise CommandError("Preserving registry state; import or review first")
            allowed = [profile(code)["slug"] for code in PROFILES]
            if entry.sources.exclude(slug__in=allowed).exists():
                raise CommandError("Preserving administrator linkage")
            for code in PROFILES:
                p = profile(code)
                existing = Source.objects.filter(slug=p["slug"]).first()
                if existing:
                    if (existing.adapter, existing.url, existing.config) != (
                        "feed",
                        p["url"],
                        p["config"],
                    ) or not entry.sources.filter(pk=existing.pk).exists():
                        raise CommandError("Preserving administrator configuration/linkage")
                    self.stdout.write(p["slug"] + ": unchanged; audit/approval preserved")
                    continue
                self.stdout.write(p["slug"] + ": prepare disabled metadata-only feed")
                if options["apply"]:
                    source = Source.objects.create(
                        slug=p["slug"],
                        name=p["title"],
                        organization=entry.organization[:120],
                        adapter="feed",
                        url=p["url"],
                        config=p["config"],
                        language="pl",
                        priority=int(entry.priority[1]),
                        interval_minutes=240,
                        scope=NOTE,
                        reuse_notes=entry.technical.get("Reuse / licence") or "",
                        enabled=False,
                        activation_approved=False,
                    )
                    entry.sources.add(source)
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
