from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.regional_rss import NOTE, PROFILES, profile
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Replace only untouched disabled regional home candidates; dry-run/no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            plan = []
            for key in PROFILES:
                p = profile(key)
                entry = RegistryEntry.objects.filter(registry_id=key).first()
                source = Source.objects.filter(slug=p["slug"]).first()
                if (
                    not entry
                    or not source
                    or entry.integration_state != "configured"
                    or list(entry.sources.values_list("pk", flat=True)) != [source.pk]
                    or list(source.registry_entries.values_list("registry_id", flat=True)) != [key]
                ):
                    raise CommandError("Preserving administrator registry linkage/state")
                if (source.adapter, source.url, source.config) == ("feed", p["url"], p["config"]):
                    plan.append((source, p, False))
                    continue
                if (
                    (source.adapter, source.url, source.config)
                    != ("web", p.get("legacy_home", p["home"]), {})
                    or source.enabled
                    or source.activation_approved
                    or source.items.exists()
                    or source.last_success_at is not None
                    or source.failures
                    or source.interval_minutes != 120
                    or source.scope
                    != (
                        "Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
                        "Bez pełnego archiwum."
                    )
                ):
                    raise CommandError("Preserving administrator configuration/history")
                plan.append((source, p, True))
            # Validate the whole package before changing any source.
            for source, p, replace in plan:
                if not replace:
                    self.stdout.write(source.slug + ": unchanged; audit/approval preserved")
                    continue
                self.stdout.write(source.slug + ": replace untouched home with disabled RSS")
                if options["apply"]:
                    source.adapter, source.url, source.config = "feed", p["url"], p["config"]
                    source.scope = NOTE
                    source.last_audit, source.audited_at = {}, None
                    source.etag = source.last_modified = ""
                    source.save(
                        update_fields=[
                            "adapter",
                            "url",
                            "config",
                            "scope",
                            "last_audit",
                            "audited_at",
                            "etag",
                            "last_modified",
                        ]
                    )
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
