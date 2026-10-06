import json
import time
from contextlib import contextmanager

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from ingestion.models import DataChannel
from ingestion.service import batch_lock
from ingestion.stac_recent import PROFILES, configuration, probe
from ingestion.transport import Client, FetchError


@contextmanager
def configuration_lock(wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        with batch_lock() as acquired:
            if acquired:
                yield
                return
        if time.monotonic() >= deadline:
            raise CommandError("Collector busy; retry later; no configuration changes.")
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def defaults(parent):
    return {
        "registry_id": parent.registry_id,
        "name": parent.name + " — świeża próbka 24h",
        "kind": "stac",
        "url": parent.url,
        "region": parent.region,
        "config": configuration(parent),
        "interval_minutes": 60,
        "attribution": parent.attribution
        + (
            " Osobna próbka ostatnich 24 godzin: maks.2 strony/20 scen co godzinę; "
            "nie pełna doba ani dokładna maska PL/UE. Historia i jej kursor bez zmian."
        ),
    }


class Command(BaseCommand):
    help = "Configure separate reviewed 24h STAC samples, dry-run/disabled by default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--wait-seconds",
            type=int,
            default=0,
            help="Wait up to 30s for batch lock, without repeating GET probes.",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Fresh two-page GET probes before approval/activation.",
        )

    def handle(self, *args, **options):
        if not 0 <= options["wait_seconds"] <= 30:
            raise CommandError("Wait budget must be between 0 and 30 seconds")
        try:
            prepared = []
            for slug in PROFILES:
                parent = DataChannel.objects.get(slug=slug)
                expected = defaults(parent)
                child = DataChannel.objects.filter(slug=slug + "-recent").first()
                if child and any(getattr(child, key) != value for key, value in expected.items()):
                    raise FetchError("Modified recent profile; preserve administrator changes")
                outcome = None
                if options["activate"] and not (child and child.enabled and child.access_approved):
                    candidate = DataChannel(slug=slug + "-recent", **expected)
                    outcome = probe(candidate, Client())
                prepared.append((parent.pk, expected, outcome))
            with configuration_lock(options["wait_seconds"]), transaction.atomic():
                rows = []
                for parent_id, expected, outcome in prepared:
                    parent = DataChannel.objects.select_for_update().get(pk=parent_id)
                    if defaults(parent) != expected:
                        raise FetchError("History profile changed during probe")
                    slug = parent.slug + "-recent"
                    child = DataChannel.objects.select_for_update().filter(slug=slug).first()
                    if child and any(
                        getattr(child, key) != value for key, value in expected.items()
                    ):
                        raise FetchError("Recent profile changed during probe")
                    if options["apply"]:
                        if child is None:
                            child = DataChannel.objects.create(slug=slug, **expected)
                        if options["activate"] and not (child.enabled and child.access_approved):
                            if outcome is None:
                                raise FetchError("Fresh successful probe required")
                            child.enabled, child.access_approved = True, True
                            child.next_fetch_at = timezone.now()
                            child.save(
                                update_fields=["enabled", "access_approved", "next_fetch_at"]
                            )
                    rows.append(
                        {
                            "slug": slug,
                            "history_channel_id": parent.pk,
                            "apply": options["apply"],
                            "activate": options["activate"],
                            "probe": outcome,
                            "history_modified": False,
                        }
                    )
            self.stdout.write(json.dumps(rows, ensure_ascii=False))
        except (DataChannel.DoesNotExist, FetchError, ValueError, TypeError, AttributeError) as exc:
            raise CommandError(
                "STAC recent unavailable: profile, policy, probe or batch lock."
            ) from exc
