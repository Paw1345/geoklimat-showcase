from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import RegistryEntry, RegistryRelease, Source, Topic
from ingestion.registry_profiles import LEGACY, profile
from ingestion.service import batch_lock
from ingestion.workbook_registry import read_registry


class Command(BaseCommand):
    help = "Import all six sheets, preserving runtime channels and audit state. Default dry-run."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to a registry workbook supplied by the operator")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        try:
            workbook = read_registry(options["path"])
        except (ValueError, KeyError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        tables = workbook["tables"]
        masters = tables["Master Registry"]
        self.stdout.write(
            f"Sources: {len(masters)}; taxonomy: {len(tables['Taxonomy'])}; "
            f"evidence: {len(tables['Verification Evidence'])}; "
            f"queue: {len(tables['Implementation Queue'])}"
        )
        if not options["apply"]:
            self.stdout.write("Dry run; no database writes or HTTP.")
            return
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            release, _ = RegistryRelease.objects.get_or_create(
                sha256=workbook["sha256"],
                defaults={"filename": workbook["filename"], "sheets": workbook["sheets"]},
            )
            technical = {row["ID"]: row for row in tables["Technical Verification"]}
            queue = {row["ID"]: row for row in tables["Implementation Queue"]}
            for row in tables["Taxonomy"]:
                Topic.objects.get_or_create(
                    slug=row["Code"],
                    defaults={"name": row["Name"], "keywords": row["Includes"].split("; ")},
                )
            for master in masters:
                key = master["ID"]
                tech = technical[key]
                route, state, note = profile(master, tech)
                entry, created = RegistryEntry.objects.get_or_create(
                    registry_id=key,
                    defaults={
                        "release": release,
                        "organization": master["Organization"],
                        "name": master["Source / Endpoint"],
                        "country": master["Country/Area"],
                        "level": master["Level"],
                        "priority": master["Priority"],
                        "technical_status": tech["Technical status"],
                        "master": master,
                        "technical": tech,
                        "queue": queue.get(key, {}),
                        "integration_state": state,
                        "integration_note": note,
                    },
                )
                if not created:
                    if entry.release_id != release.pk:
                        # Do not silently mix a new register with old channel configuration.
                        raise CommandError(
                            "New workbook version conflicts with current registry; "
                            "review migration explicitly"
                        )
                    continue
                legacy = list(Source.objects.filter(slug__in=LEGACY.get(key, [])))
                if legacy:
                    entry.sources.set(legacy)
                    entry.integration_state = "derived" if key == 61 else "linked"
                    entry.integration_note = (
                        "Powiązano istniejące kanały i zachowano historię/adresy z audytu. "
                        + (
                            "Filtr Konsultacje na GDOŚ."
                            if key == 61
                            else "Zakres kanału może być węższy niż pozycja rejestru."
                        )
                    )
                    entry.save()
                    if key != 61:
                        for source in legacy:
                            source.priority = min(source.priority, int(master["Priority"][1]))
                            source.interval_minutes = min(
                                source.interval_minutes, master["Poll interval (min)"]
                            )
                            source.save(update_fields=["priority", "interval_minutes"])
                elif route:
                    adapter, url, config = route
                    source, _ = Source.objects.get_or_create(
                        slug=f"registry-{key:03d}",
                        defaults={
                            "name": f"{master['Organization']} — {master['Source / Endpoint']}"[
                                :160
                            ],
                            "organization": master["Organization"][:120],
                            "adapter": adapter,
                            "url": url,
                            "config": config,
                            "language": "pl" if master["Country/Area"] == "Polska" else "en",
                            "priority": int(master["Priority"][1]),
                            "interval_minutes": master["Poll interval (min)"],
                            "scope": note,
                            "reuse_notes": tech["Reuse / licence"] or "",
                            "enabled": False,
                            "activation_approved": False,
                        },
                    )
                    entry.sources.add(source)
            self.stdout.write(
                f"Saved release {release.sha256}; entries: {RegistryEntry.objects.count()}; "
                f"runtime channels: {Source.objects.count()}. New channels remain disabled."
            )
