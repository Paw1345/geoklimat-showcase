from datetime import timedelta
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ingestion.models import DuplicateCandidate, Item, ItemVersion, Publication
from ingestion.normalization import normalized_fields, title_key

SNAPSHOT_FIELDS = (
    "title",
    "url",
    "summary",
    "published_at",
    "source_updated_at",
    "content_hash",
    "metadata",
    "canonical_url",
    "normalized_title",
    "publisher",
    "authors",
    "date_precision",
)


def snapshot(item):
    values = {field: getattr(item, field) for field in SNAPSHOT_FIELDS}
    for field in ("published_at", "source_updated_at"):
        if values[field]:
            values[field] = values[field].isoformat()
    values["source_id"] = item.source_id
    return values


def record_version(item, origin="ingestion"):
    current = snapshot(item)
    previous = item.versions.first()
    if previous and previous.snapshot == current:
        return False
    number = (previous.number if previous else 0) + 1
    changed = [key for key in current if not previous or previous.snapshot.get(key) != current[key]]
    ItemVersion.objects.create(
        item=item, number=number, snapshot=current, changed_fields=changed, origin=origin
    )
    item.revision = number
    item.save(update_fields=["revision"])
    return True


def choose_primary(publication):
    if (
        publication.primary_confirmed
        and publication.items.filter(pk=publication.primary_item_id).exists()
    ):
        return
    # Editorial ranking determines only a representative; it does NOT prove original authorship.
    primary = (
        publication.items.select_related("source")
        .order_by("source__priority", "first_seen_at", "pk")
        .first()
    )
    publication.primary_item = primary
    publication.primary_confirmed = False
    publication.primary_note = (
        "Reprezentant według priorytetu kanału i czasu wykrycia; pierwotność niepotwierdzona."
    )
    publication.save(update_fields=["primary_item", "primary_confirmed", "primary_note"])


def merge_groups(ids):
    groups = list(Publication.objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    target = groups[0]
    confirmed_notes = {
        group.primary_item_id: group.primary_note for group in groups if group.primary_confirmed
    }
    # Preserve confirmed attribution if only one distinct primary has been confirmed.
    confirmed = {
        group.primary_item_id
        for group in groups
        if group.primary_confirmed and group.primary_item_id
    }
    for group in groups[1:]:
        Item.objects.filter(publication=group).update(publication=target)
        group.primary_item = None
        group.primary_confirmed = False
        group.save(update_fields=["primary_item", "primary_confirmed"])
    if len(confirmed) == 1:
        target.primary_item_id = confirmed.pop()
        target.primary_confirmed = True
        target.primary_note = confirmed_notes[target.primary_item_id]
    elif len(confirmed) > 1:
        target.primary_confirmed = False
    target.save()
    # Empty groups remain as non-destructive historical shells; public pages redirect by item.
    choose_primary(target)
    return target


def assign_publication(item):
    query = Q(canonical_url=item.canonical_url)
    if item.duplicate_key:
        query |= Q(duplicate_key=item.duplicate_key)
    isolated_kinds = ["funding_call", "news_notice", "service_capabilities"]
    if item.metadata.get("record_kind") in isolated_kinds:
        # Independent calls and WMS layers can share one endpoint URL. Only the
        # source record identity (unique_source_item) identifies each record.
        matching = Item.objects.none()
    else:
        ordinary = Q(metadata__record_kind__isnull=True) | ~Q(
            metadata__record_kind__in=isolated_kinds
        )
        matching = (
            Item.objects.filter(query, ordinary).exclude(pk=item.pk).exclude(publication=None)
        )
    ids = set(matching.values_list("publication_id", flat=True))
    if item.publication_id:
        ids.add(item.publication_id)
    if not ids:
        publication = Publication.objects.create()
    else:
        publication = merge_groups(ids)
    item.publication = publication
    item.save(update_fields=["publication"])
    choose_primary(publication)


def enrich(item, origin="ingestion"):
    fields = normalized_fields(item)
    for key, value in fields.items():
        setattr(item, key, value)
    item.save(update_fields=list(fields))
    record_version(item, origin)
    assign_publication(item)


def suggest_duplicates(item):
    if (
        not item.published_at
        or len(item.normalized_title) < 40
        or item.source.adapter not in {"feed", "html"}
    ):
        return
    candidates = (
        Item.objects.filter(
            published_at__range=(
                item.published_at - timedelta(days=2),
                item.published_at + timedelta(days=2),
            ),
            source__language=item.source.language,
            source__adapter__in=["feed", "html"],
        )
        .exclude(source=item.source)
        .exclude(publication_id=item.publication_id)
    )
    for other in candidates.iterator():
        title_score = SequenceMatcher(
            None, item.normalized_title, other.normalized_title, autojunk=False
        ).ratio()
        if title_score < 0.92 or min(len(item.summary), len(other.summary)) < 80:
            continue
        summary_score = SequenceMatcher(
            None, title_key(item.summary), title_key(other.summary), autojunk=False
        ).ratio()
        if summary_score < 0.8:
            continue
        left, right = sorted([item, other], key=lambda value: value.pk)
        defaults = {
            "score": round((title_score + summary_score) / 2, 4),
            "left_revision": left.revision,
            "right_revision": right.revision,
        }
        candidate, created = DuplicateCandidate.objects.get_or_create(
            left=left, right=right, defaults=defaults
        )
        if not created and candidate.status == "pending":
            DuplicateCandidate.objects.filter(pk=candidate.pk).update(**defaults)


@transaction.atomic
def review_candidate(candidate_id, accept, reviewer):
    candidate = DuplicateCandidate.objects.select_for_update().get(pk=candidate_id)
    if candidate.status != "pending":
        return
    items = list(
        Item.objects.select_for_update()
        .filter(pk__in=[candidate.left_id, candidate.right_id])
        .order_by("pk")
    )
    if accept:
        if [item.revision for item in items] != [candidate.left_revision, candidate.right_revision]:
            raise ValueError("Publikacje zmieniły się po analizie. Wymagana ponowna ocena.")
        if any(not item.publication_id for item in items):
            raise ValueError("Najpierw uruchom normalizację danych.")
        merge_groups({item.publication_id for item in items})
    candidate.status = "accepted" if accept else "rejected"
    candidate.reviewed_at = timezone.now()
    candidate.reviewed_by = reviewer[:150]
    candidate.save()
