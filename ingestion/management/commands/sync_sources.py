from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, Source
from ingestion.service import batch_lock
from ingestion.source_catalogue import FROM_REGISTRY, CatalogueError, load

# Reset on replace, as the old configure_* commands do: a new route needs a new audit.
AUDIT_RESET = {"last_audit": {}, "audited_at": None, "etag": "", "last_modified": ""}


def untouched(source):
    return not (
        source.enabled
        or source.activation_approved
        or source.failures
        or source.last_success_at is not None
        or source.items.exists()
        or source.runs.exists()
    )


def matches(source, previous):
    return all(getattr(source, key) == value for key, value in previous.items())


def interval(spec, entry):
    if spec.interval_minutes != FROM_REGISTRY:
        return spec.interval_minutes
    value = entry.master.get("Poll interval (min)")
    if type(value) is not int or not 15 <= value <= 10080:
        raise CommandError(f"{spec.slug}: registry poll interval invalid")
    return value


def language(spec, entry):
    if spec.language != FROM_REGISTRY:
        return spec.language
    return "pl" if entry.country == "Polska" else "en"


def mark_registry(spec, entry):
    if spec.registry_state:
        entry.integration_state, entry.integration_note = spec.registry_state, spec.scope
        entry.save(update_fields=["integration_state", "integration_note"])


class Command(BaseCommand):
    help = (
        "Compare sources/sources.yaml with Source rows; dry-run by default, no HTTP. "
        "--apply creates missing disabled sources and replaces untouched old routes. "
        "Any administrator change stops the whole run before writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--group", action="append", default=[], help="Limit to group(s)")
        parser.add_argument("--catalogue", help="Alternative YAML path (tests, review)")

    def handle(self, *args, **options):
        try:
            specs = load(options["catalogue"])
        except (CatalogueError, OSError) as exc:
            raise CommandError(f"Catalogue invalid: {exc}") from exc
        groups = set(options["group"])
        unknown = groups - {spec.group for spec in specs}
        if unknown:
            raise CommandError(f"Unknown group(s): {', '.join(sorted(unknown))}")
        by_registry = {}
        for spec in specs:
            by_registry.setdefault(spec.registry, set()).add(spec.slug)
        selected = [spec for spec in specs if not groups or spec.group in groups]
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            plan = [(spec, *self.decide(spec, by_registry[spec.registry])) for spec in selected]
            counts = {}
            for spec, action, detail in plan:
                counts[action] = counts.get(action, 0) + 1
                self.stdout.write(
                    f"{spec.group}/{spec.slug}: {action}" + (f"; {detail}" * bool(detail))
                )
            summary = ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))
            self.stdout.write(f"Summary: {summary}")
            if counts.get("conflict"):
                raise CommandError("Conflicts preserved; nothing written")
            if not options["apply"]:
                self.stdout.write("Dry-run; no database writes or HTTP")
                return
            for spec, action, _ in plan:
                if action == "create":
                    self.create(spec)
                elif action == "replace":
                    self.replace(spec)

    def decide(self, spec, catalogue_slugs):
        entry = RegistryEntry.objects.filter(registry_id=spec.registry).first()
        if entry is None:
            return "conflict", "registry entry missing; import the registry first"
        if spec.registry_requires and entry.integration_state not in spec.registry_requires:
            return "conflict", f"registry state {entry.integration_state} preserved"
        if entry.sources.exclude(slug__in=catalogue_slugs).exists():
            return "conflict", "registry entry has administrator sources"
        source = Source.objects.filter(slug=spec.slug).first()
        if source is None:
            return "create", "disabled, not approved"
        linked = list(source.registry_entries.values_list("registry_id", flat=True))
        if linked != [spec.registry]:
            return "conflict", f"registry linkage {linked} preserved"
        if (source.adapter, source.url, source.config) == spec.identity:
            return "unchanged", ""
        if untouched(source) and any(matches(source, old) for old in spec.replaces):
            return "replace", f"{source.adapter} {source.url} -> {spec.adapter} {spec.url}"
        return "conflict", "administrator configuration or history preserved"

    def create(self, spec):
        entry = RegistryEntry.objects.get(registry_id=spec.registry)
        source = Source.objects.create(
            slug=spec.slug,
            name=spec.name.format(organization=entry.organization, name=entry.name)[:160],
            organization=entry.organization[:120],
            adapter=spec.adapter,
            url=spec.url,
            config=dict(spec.config),
            language=language(spec, entry),
            priority=int(entry.priority[1]),
            interval_minutes=interval(spec, entry),
            scope=spec.scope,
            reuse_notes=(
                spec.reuse_notes
                if spec.reuse_notes is not None
                else entry.technical.get("Reuse / licence") or ""
            ),
            enabled=False,
            activation_approved=False,
        )
        entry.sources.add(source)
        mark_registry(spec, entry)

    def replace(self, spec):
        source = Source.objects.get(slug=spec.slug)
        source.adapter, source.url, source.config = spec.adapter, spec.url, dict(spec.config)
        fields = ["adapter", "url", "config", *AUDIT_RESET]
        if spec.scope:
            source.scope = spec.scope
            fields.append("scope")
        for key, value in AUDIT_RESET.items():
            setattr(source, key, value)
        source.save(update_fields=fields)
        mark_registry(spec, RegistryEntry.objects.get(registry_id=spec.registry))
