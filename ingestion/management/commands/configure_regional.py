from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.regional_profiles import NOTE, PROFILES
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Configure reviewed regional news sections; dry-run default, no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entries = {
                e.registry_id: e for e in RegistryEntry.objects.filter(registry_id__in=PROFILES)
            }
            if set(entries) != set(PROFILES):
                raise CommandError("Import the registry first")
            for key, (adapter, url, config) in PROFILES.items():
                entry, slug = entries[key], f"registry-{key:03d}"
                existing = Source.objects.filter(slug=slug).first()
                if existing:
                    # Supersede only the exact unused candidate created by this command.
                    previous = (
                        key == 137
                        and existing.adapter == "feed"
                        and existing.url == url + "feed/"
                        and existing.config == {"strict_endpoint": True}
                        and not existing.enabled
                        and not existing.activation_approved
                        and not existing.items.exists()
                        and entry.sources.filter(pk=existing.pk).exists()
                    )
                    if previous:
                        self.stdout.write(
                            f"{slug}: replace invalid RSS candidate with reviewed HTML"
                        )
                        if options["apply"]:
                            existing.adapter, existing.url, existing.config = (
                                adapter,
                                url,
                                config.copy(),
                            )
                            existing.last_audit, existing.audited_at = {}, None
                            existing.etag = existing.last_modified = ""
                            existing.save()
                        continue
                    if (existing.adapter, existing.url, existing.config) != (
                        adapter,
                        url,
                        config,
                    ) or not entry.sources.filter(pk=existing.pk).exists():
                        raise CommandError("Preserving administrator configuration or linkage")
                    self.stdout.write(f"{slug}: unchanged; audit and activation preserved")
                    continue
                if entry.sources.exists():
                    raise CommandError("Preserving existing registry linkage")
                self.stdout.write(f"{slug}: create disabled thematic news channel")
                if not options["apply"]:
                    continue
                source = Source.objects.create(
                    slug=slug,
                    name=f"{entry.organization} — środowisko"[:160],
                    organization=entry.organization[:120],
                    adapter=adapter,
                    url=url,
                    config=config.copy(),
                    language="pl",
                    priority=int(entry.priority[1]),
                    interval_minutes=120,
                    scope=NOTE,
                    reuse_notes=entry.technical.get("Reuse / licence") or "",
                    enabled=False,
                    activation_approved=False,
                )
                entry.sources.add(source)
                entry.integration_state, entry.integration_note = "configured", NOTE
                entry.save(update_fields=["integration_state", "integration_note"])
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
