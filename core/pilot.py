"""Read-only editorial acceptance worksheet; never an automatic sign-off."""

import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from ingestion.changes import require_editor
from ingestion.models import Item


def baseline():
    path = settings.BASE_DIR / "docs" / "pilot-sample-2026-09-25.json"
    return json.loads(path.read_text(encoding="utf-8"))


@require_GET
@login_required
@never_cache
def dashboard(request):
    require_editor(request.user)
    snapshot = baseline()
    originals = [(group["group"], row) for group in snapshot["groups"] for row in group["items"]]
    items = Item.objects.select_related("source", "classification").in_bulk(
        [row["id"] for _, row in originals]
    )
    rows = []
    for group, original in originals:
        item = items.get(original["id"])
        # IDs are installation-specific; never compare an unrelated fresh-install row.
        matches = bool(
            item and item.url == original["url"] and item.source.slug == original["source"]
        )
        rows.append(
            {
                "group": group,
                "original": original,
                "item": item if matches else None,
                "changed": matches and item.content_hash != original["hash"],
            }
        )
    return render(request, "pilot.html", {"rows": rows, "sample_date": snapshot["generated_at"]})
