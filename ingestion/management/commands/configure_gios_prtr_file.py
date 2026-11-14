import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.gios_prtr_file import ATTRIBUTION, CONFIG, SLUG, URL, audit_file
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Audit exact E-PRTR 2017 original; dry-run default; never queue a download."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=49).first()
            if not entry:
                raise CommandError("Import registry49 first")
            existing = DataChannel.objects.filter(slug=SLUG).first()
            if existing:
                if (
                    existing.registry_id,
                    existing.kind,
                    existing.url,
                    existing.config,
                    existing.region,
                    existing.attribution,
                ) != (entry.pk, "file", URL, CONFIG, "PL", ATTRIBUTION):
                    raise CommandError("Preserving administrator configuration or linkage")
                self.stdout.write("Unchanged; existing approval, audit and jobs preserved")
                return
            try:
                result = audit_file(Client())
            except (FetchError, ValueError, KeyError, TypeError, AttributeError) as exc:
                raise CommandError("PRTR file audit failed; configuration unchanged") from exc
            self.stdout.write(json.dumps(result, ensure_ascii=False))
            if not options["apply"]:
                self.stdout.write("Dry-run: no database writes, download jobs or full file fetch")
                return
            DataChannel.objects.create(
                registry=entry,
                slug=SLUG,
                name="GIOŚ — E-PRTR Polska2017 (oryginalny XML historyczny)",
                kind="file",
                url=URL,
                config=CONFIG.copy(),
                region="PL",
                attribution=ATTRIBUTION,
                access_approved=True,
                enabled=False,
                last_status="verified",
                checked_at=timezone.now(),
            )
            self.stdout.write("Created on-demand file profile; registry unchanged; no job queued")
