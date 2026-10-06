"""Associate existing ELI collectors; never create or mutate Source records."""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.catalog import CATALOG
from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Link registry23 to existing DU/MP collectors; dry-run default/no HTTP."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            registry = RegistryEntry.objects.select_for_update().filter(registry_id=23).first()
            sources = list(Source.objects.select_for_update().filter(slug__in=["eli-du", "eli-mp"]))
            legacy = Source.objects.select_for_update().filter(slug="registry-023").first()
            if not registry or not legacy or len(sources) != 2:
                raise CommandError("Missing existing ELI registry/collectors; no sources created")
            profiles = {row["slug"]: row for row in CATALOG if row["slug"] in {"eli-du", "eli-mp"}}
            for source in sources:
                profile = profiles[source.slug]
                if (source.adapter, source.url, source.config, source.scope) != (
                    "eli",
                    profile["url"],
                    {},
                    profile["scope"],
                ) or set(source.registry_entries.values_list("registry_id", flat=True)) not in (
                    {22},
                    {22, 23},
                ):
                    raise CommandError("Preserving administrator collector configuration/linkage")
            actual = set(registry.sources.values_list("pk", flat=True))
            target = {source.pk for source in sources}
            if actual == target:
                self.stdout.write("Registry23: unchanged; all source fields/history preserved")
                return
            if (
                actual != {legacy.pk}
                or registry.integration_state != "configured"
                or (
                    legacy.adapter,
                    legacy.url,
                    legacy.config,
                    legacy.enabled,
                    legacy.activation_approved,
                    legacy.failures,
                    legacy.last_success_at,
                )
                != ("web", "https://eli.gov.pl/", {}, False, False, 0, None)
                or (
                    set(legacy.registry_entries.values_list("registry_id", flat=True)) != {23}
                    or legacy.items.exists()
                    or legacy.runs.exists()
                )
            ):
                raise CommandError("Preserving administrator legacy configuration/history/linkage")
            for source in sources:
                audit = source.last_audit
                if (
                    not source.enabled
                    or not source.activation_approved
                    or source.failures
                    or (
                        not source.audited_at
                        or source.audited_at < timezone.now() - timedelta(minutes=15)
                        or source.audited_at > timezone.now()
                        or audit.get("status") != "verified"
                        or type(audit.get("count")) is not int
                        or audit["count"] <= 0
                        or audit.get("rejected") != 0
                    )
                ):
                    raise CommandError("Both existing collectors need fresh verified audits")
            self.stdout.write("Registry23: replace untouched homepage linkage with existing DU/MP")
            if not options["apply"]:
                self.stdout.write("Dry-run; no writes or HTTP")
                return
            registry.sources.set(sources)
            self.stdout.write(
                "Linked; legacy Source retained, registry22 and source fields untouched"
            )
