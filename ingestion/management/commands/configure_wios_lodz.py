from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock
from ingestion.wios_lodz import HOME, NOTE, PROFILES, profile


class Command(BaseCommand):
    help = "Prepare two disabled WIOS Lodz lists; preserve old identity/admin/history."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            entry = RegistryEntry.objects.filter(registry_id=86).first()
            allowed = [profile(code)["slug"] for code in PROFILES]
            if (
                not entry
                or entry.integration_state != "configured"
                or entry.sources.exclude(slug__in=allowed).exists()
            ):
                raise CommandError("Preserving administrator registry linkage/state")
            plan = []
            for code in PROFILES:
                p = profile(code)
                source = Source.objects.filter(slug=p["slug"]).first()
                if source and (
                    not entry.sources.filter(pk=source.pk).exists()
                    or list(source.registry_entries.values_list("registry_id", flat=True)) != [86]
                ):
                    raise CommandError("Preserving administrator source linkage")
                if source and (source.adapter, source.url, source.config) == (
                    "web",
                    p["url"],
                    p["config"],
                ):
                    plan.append((p, source, "unchanged"))
                    continue
                if code == "news":
                    if (
                        not source
                        or (source.adapter, source.url, source.config) != ("web", HOME, {})
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
                    plan.append((p, source, "replace"))
                elif source:
                    raise CommandError("Preserving administrator notices configuration")
                else:
                    plan.append((p, None, "create"))
            for p, source, action in plan:
                self.stdout.write(p["slug"] + ": " + action + "; no automatic activation")
                if not options["apply"] or action == "unchanged":
                    continue
                if action == "replace":
                    source.url, source.config, source.scope = p["url"], p["config"], NOTE
                    source.last_audit, source.audited_at = {}, None
                    source.etag = source.last_modified = ""
                    source.save(
                        update_fields=[
                            "url",
                            "config",
                            "scope",
                            "last_audit",
                            "audited_at",
                            "etag",
                            "last_modified",
                        ]
                    )
                else:
                    source = Source.objects.create(
                        slug=p["slug"],
                        name=entry.organization[:120] + " — " + p["title"],
                        organization=entry.organization[:120],
                        adapter="web",
                        url=p["url"],
                        config=p["config"],
                        language="pl",
                        priority=int(entry.priority[1]),
                        interval_minutes=120,
                        scope=NOTE,
                        reuse_notes=entry.technical.get("Reuse / licence") or "",
                        enabled=False,
                        activation_approved=False,
                    )
                    entry.sources.add(source)
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
