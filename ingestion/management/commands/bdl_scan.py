import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.bdl_scan import SLUG, advance, defaults, validate_profile
from ingestion.models import DataChannel, RegistryEntry
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError


class Command(BaseCommand):
    help = "Prepare a disabled manual RDLP scan, or fetch one pinned geometry."

    def add_arguments(self, parser):
        parser.add_argument("--prepare", action="store_true")
        parser.add_argument("--fetch", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--on-demand", action="store_true")

    def handle(self, *args, **options):
        if options["on_demand"] and (not options["fetch"] or options["prepare"]):
            raise CommandError("On-demand requires explicit fetch, never prepare")
        if options["prepare"] and options["fetch"]:
            raise CommandError("Choose prepare or fetch, never both")
        if options["apply"] and not (options["prepare"] or options["fetch"]):
            raise CommandError("Apply requires explicit prepare or fetch")
        if not options["prepare"] and not options["fetch"]:
            self.stdout.write(
                "Plan: separate disabled scan, one geometry/id, 5MiB ceiling; no HTTP/DB"
            )
            return
        with batch_lock() as acquired:
            if not acquired:
                raise CommandError("Ingestion busy; no changes")
            try:
                if options["prepare"]:
                    with transaction.atomic():
                        registry = RegistryEntry.objects.select_for_update().get(registry_id=39)
                        channel = (
                            DataChannel.objects.select_related("registry").filter(slug=SLUG).first()
                        )
                        if channel:
                            validate_profile(channel)
                        elif options["apply"]:
                            channel = DataChannel.objects.create(
                                slug=SLUG,
                                name="BDL — RDLP, przebieg ręczny",
                                enabled=False,
                                access_approved=False,
                                **defaults(registry),
                            )
                        self.stdout.write(
                            "Prepared disabled/unapproved"
                            if options["apply"] and channel and not channel.access_approved
                            else "Profile preserved / dry-run"
                        )
                        return
                channel = DataChannel.objects.select_related("registry").get(slug=SLUG)
                result = advance(
                    channel, Client(), dry_run=not options["apply"], on_demand=options["on_demand"]
                )
            except (FetchError, DataChannel.DoesNotExist, RegistryEntry.DoesNotExist) as exc:
                raise CommandError(
                    "BDL scan stopped; cursor preserved. "
                    "Review profile, policy, manifest or response budget."
                ) from exc
            self.stdout.write(json.dumps(result, ensure_ascii=False))
