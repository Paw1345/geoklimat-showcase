from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from ingestion.changes import ensure_thread
from ingestion.legislation import calendar_date, fingerprint
from ingestion.models import Item, LegalEvent, LegalLink, LegalProcess
from ingestion.service import batch_lock
from ingestion.transport import FetchError


class Command(BaseCommand):
    help = (
        "Link an item and record an evidence-backed date; preview default, preserves corrections."
    )

    def add_arguments(self, parser):
        parser.add_argument("--item", type=int, required=True)
        parser.add_argument("--process", required=True, help="Stable identifier, e.g. EU:2025_393")
        parser.add_argument("--title", required=True)
        parser.add_argument("--jurisdiction", choices=["PL", "EU"], required=True)
        parser.add_argument("--kind", choices=[kind for kind, _ in LegalEvent.KINDS])
        parser.add_argument("--key", help="Stable key for corrections of this same event")
        parser.add_argument("--date")
        parser.add_argument(
            "--link-only",
            action="store_true",
            help="Link a document without inventing a missing date",
        )
        parser.add_argument("--evidence-url", required=True)
        parser.add_argument(
            "--evidence",
            required=True,
            help="Source passage/section, including scope and exceptions",
        )
        parser.add_argument("--reviewer", required=True)
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **o):
        import re

        if o["link_only"] and any(o[field] for field in ("kind", "key", "date")):
            raise CommandError("--link-only cannot include event fields")
        if not o["link_only"] and o["kind"] not in dict(LegalEvent.KINDS):
            raise CommandError("--kind is required unless --link-only")
        try:
            day = None if o["link_only"] else calendar_date(o["date"], "date")
            URLValidator(schemes=["https"])(o["evidence_url"])
        except (FetchError, ValidationError) as exc:
            raise CommandError(
                "A valid YYYY-MM-DD date and HTTPS evidence URL are required"
            ) from exc
        if not re.fullmatch(r"[A-Za-z0-9:/_.-]{1,120}", o["process"]) or (
            not o["link_only"] and not re.fullmatch(r"[A-Za-z0-9:_.-]{1,120}", o["key"] or "")
        ):
            raise CommandError("Invalid stable process/event identifier")
        if (
            not 10 <= len(o["evidence"].strip()) <= 2000
            or not 1 <= len(o["reviewer"].strip()) <= 150
            or not 1 <= len(o["title"].strip()) <= 1000
            or len(o["evidence_url"]) > 1500
        ):
            raise CommandError("Evidence, reviewer and title must be nonempty and within limits")
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Ingestion busy; retry later")
            try:
                item = Item.objects.get(pk=o["item"])
            except Item.DoesNotExist as exc:
                raise CommandError("Item not found") from exc
            process = LegalProcess.objects.filter(identifier=o["process"]).first()
            if process and process.jurisdiction != o["jurisdiction"]:
                raise CommandError("Existing jurisdiction differs; process preserved")
            self.stdout.write(
                f"{o['process']}: {o['kind']} {day}; item={item.pk}; applied={o['apply']}"
            )
            if not o["apply"]:
                return
            if process is None:
                process = LegalProcess.objects.create(
                    identifier=o["process"], title=o["title"], jurisdiction=o["jurisdiction"]
                )
            values = {
                "evidence_url": o["evidence_url"],
                "evidence": o["evidence"],
                "reviewer": o["reviewer"],
                "item_hash": item.content_hash,
            }
            ensure_thread(process)
            link, _ = LegalLink.objects.get_or_create(
                item=item,
                process=process,
                origin="editorial",
                defaults=dict(values, evidence_hash=fingerprint(values)),
            )
            if o["link_only"]:
                if link.evidence_hash != fingerprint(values):
                    raise CommandError(
                        "Existing editorial link evidence differs; preserved for review"
                    )
                return
            event_values = dict(values, kind=o["kind"], date=day, description=o["title"])
            old = link.events.filter(event_key=o["key"], superseded_at__isnull=True).first()
            if old and all(getattr(old, key) == value for key, value in event_values.items()):
                self.stdout.write("Unchanged; no new history entry")
                return
            if old:
                old.superseded_at = timezone.now()
                old.save(update_fields=["superseded_at"])
            LegalEvent.objects.create(link=link, event_key=o["key"], **event_values)
            for key, value in values.items():
                setattr(link, key, value)
            link.evidence_hash = fingerprint(values)
            link.observed_at = timezone.now()
            link.save()
