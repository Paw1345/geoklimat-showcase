"""Read-only, repeatable pilot sample; never publishes reviews or calls AI."""

import json

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ingestion.changes import threads_for_item
from ingestion.models import Item

GROUPS = {
    "MKiŚ": ("mkis", "registry-003"),
    "GIOŚ": ("gios", "registry-007"),
    "GUGiK": ("gugik", "registry-010"),
    "INSPIRE": ("registry-178", "registry-179", "inspire-gugik"),
    "UE": ("ec-climate", "ec-environment", "eea-news"),
}


def sample():
    groups = []
    for name, slugs in GROUPS.items():
        pools = [
            list(
                Item.objects.filter(source__slug=slug)
                .select_related("source", "classification")
                .order_by("-first_seen_at", "-pk")[:6]
            )
            for slug in slugs
        ]
        chosen = []
        # Round-robin source coverage; empty sources never get fabricated records.
        for index in range(6):
            for pool in pools:
                if index < len(pool) and len(chosen) < 6:
                    item = pool[index]
                    classification = getattr(item, "classification", None)
                    chosen.append(
                        {
                            "id": item.pk,
                            "source": item.source.slug,
                            "title": item.title,
                            "summary": item.summary,
                            "url": item.url,
                            "hash": item.content_hash,
                            "published_at": item.published_at,
                            "ai_eligible": len(item.summary.strip()) >= 80,
                            "threads": list(threads_for_item(item).values_list("pk", flat=True)),
                            "state": classification.state_label if classification else None,
                            "kind": classification.kind if classification else None,
                            "topics": classification.topic_labels if classification else [],
                        }
                    )
        groups.append({"group": name, "missing": 6 - len(chosen), "items": chosen})
    return {"generated_at": timezone.now().isoformat(), "groups": groups}


class Command(BaseCommand):
    help = "Read-only sample: up to six stored records per pilot area; no network or DB writes."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(json.dumps(sample(), ensure_ascii=False, default=str, indent=2))
