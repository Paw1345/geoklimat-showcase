import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.gios_noise_file import ATTRIBUTION, CONFIG, SLUG, URL, audit_file
from ingestion.models import DataChannel, RegistryEntry
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Audit exact historical GIOS noise file; dry-run default; never queue a download."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        entry = RegistryEntry.objects.filter(registry_id=48).first()
        if not entry:
            raise CommandError("Import registry48 first")
        try:
            result = audit_file(Client())
        except (FetchError, ValueError, KeyError, TypeError) as exc:
            raise CommandError("GIOS file audit failed; configuration unchanged") from exc
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        if not options["apply"]:
            self.stdout.write("Dry-run: no database writes, download jobs or full file fetch")
            return
        with transaction.atomic():
            entry = RegistryEntry.objects.select_for_update().get(pk=entry.pk)
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
                    raise CommandError("Preserving administrator configuration")
                self.stdout.write("Unchanged; existing approval and jobs preserved")
                return
            DataChannel.objects.create(
                registry=entry,
                slug=SLUG,
                name="GIOŚ — hałas drogowy2024 (plik historyczny)",
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
            self.stdout.write(
                "Created on-demand file profile; registry state unchanged; no job queued"
            )
