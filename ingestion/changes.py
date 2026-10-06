"""Deterministic membership; no title similarity or invented impact assessments."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from ingestion.models import (
    ChangeThread,
    Item,
    LegalLink,
    LegalRelation,
    ThreadDocument,
    ThreadImpact,
)


def ensure_thread(process):
    return ChangeThread.objects.get_or_create(process=process, defaults={"title": process.title})[0]


def thread_relations(thread):
    if not thread.process_id:
        return LegalRelation.objects.none()
    # Only explicit project -> act relations extend membership, one hop, no transitive merge.
    return LegalRelation.objects.filter(
        Q(link__process_id=thread.process_id) | Q(target_id=thread.process_id),
        kind="published_as",
        superseded_at__isnull=True,
        item_hash=F("link__item__content_hash"),
        link__item_hash=F("link__item__content_hash"),
    ).select_related("link__item", "link__process", "target")


def thread_process_ids(thread):
    ids = {thread.process_id} if thread.process_id else set()
    for relation in thread_relations(thread):
        ids.update((relation.link.process_id, relation.target_id))
    return ids


def thread_items(thread):
    automatic = LegalLink.objects.filter(
        process_id__in=thread_process_ids(thread),
        item_hash=F("item__content_hash"),
    ).values("item_id")
    decisions = ThreadDocument.objects.filter(thread=thread, superseded_at__isnull=True)
    included = decisions.filter(included=True, item_hash=F("item__content_hash")).values("item_id")
    # A stale manual inclusion is NOT replaced silently by an automatic link.
    overridden = decisions.values("item_id")
    return (
        Item.objects.filter(Q(pk__in=included) | (Q(pk__in=automatic) & ~Q(pk__in=overridden)))
        .select_related("source")
        .order_by(F("published_at").asc(nulls_last=True), "pk")
    )


def require_editor(user):
    if not user.is_active or not user.has_perm("ingestion.edit_change_threads"):
        raise PermissionDenied


def threads_for_item(item):
    processes = LegalLink.objects.filter(item=item, item_hash=item.content_hash).values(
        "process_id"
    )
    relations = LegalRelation.objects.filter(
        kind="published_as",
        superseded_at__isnull=True,
        item_hash=F("link__item__content_hash"),
        link__item_hash=F("link__item__content_hash"),
    )
    decisions = ThreadDocument.objects.filter(
        thread_id=OuterRef("pk"), item=item, superseded_at__isnull=True
    )
    return (
        ChangeThread.objects.annotate(
            overridden=Exists(decisions),
            manual_included=Exists(decisions.filter(included=True, item_hash=item.content_hash)),
        )
        .filter(
            Q(manual_included=True)
            | (
                Q(overridden=False)
                & (
                    Q(process_id__in=processes)
                    | Q(
                        process_id__in=relations.filter(target_id__in=processes).values(
                            "link__process_id"
                        )
                    )
                    | Q(
                        process_id__in=relations.filter(link__process_id__in=processes).values(
                            "target_id"
                        )
                    )
                )
            )
        )
        .select_related("process")
    )


@transaction.atomic
def decide_document(thread, item, user, included, reason, expected_hash):
    require_editor(user)
    ChangeThread.objects.select_for_update().get(pk=thread.pk)
    item = Item.objects.select_for_update().get(pk=item.pk)
    if item.content_hash != expected_hash:
        raise ValidationError("Dokument zmienił się. Odśwież podgląd i ponownie sprawdź dowód.")
    if not 10 <= len(reason.strip()) <= 2000:
        raise ValidationError("Uzasadnienie musi mieć 10–2000 znaków.")
    current = ThreadDocument.objects.filter(thread=thread, item=item, superseded_at__isnull=True)
    previous = current.first()
    if previous and (previous.included, previous.reason, previous.item_hash) == (
        included,
        reason.strip(),
        expected_hash,
    ):
        return previous
    current.update(superseded_at=timezone.now())
    return ThreadDocument.objects.create(
        thread=thread,
        item=item,
        included=included,
        reason=reason.strip(),
        item_hash=expected_hash,
        author=user,
    )


@transaction.atomic
def assess(thread, item, user, expected_hash, previous_id, **fields):
    require_editor(user)
    ChangeThread.objects.select_for_update().get(pk=thread.pk)
    item = Item.objects.select_for_update().get(pk=item.pk)
    if item.content_hash != expected_hash or not thread_items(thread).filter(pk=item.pk).exists():
        raise ValidationError("Dokument zmienił się lub nie należy już do wątku.")
    URLValidator(schemes=["https"])(fields["evidence_url"])
    for key in ("change", "audience", "evidence"):
        fields[key] = fields[key].strip()
        if not 10 <= len(fields[key]) <= 3000:
            raise ValidationError("Opis, odbiorcy i dowód muszą mieć 10–3000 znaków.")
    kinds = fields["kinds"]
    if not isinstance(kinds, list) or not kinds or set(kinds) - dict(ThreadImpact.KINDS).keys():
        raise ValidationError("Wybierz poprawny rodzaj wpływu.")
    fields["kinds"] = sorted(set(kinds))
    current = ThreadImpact.objects.filter(thread=thread, superseded_at__isnull=True)
    old = current.first()
    if (
        old
        and old.item_id == item.pk
        and old.item_hash == expected_hash
        and all(getattr(old, key) == value for key, value in fields.items())
    ):
        return old
    if (old.pk if old else 0) != previous_id:
        raise ValidationError("Inny redaktor zmienił ocenę. Odśwież formularz.")
    current.update(superseded_at=timezone.now())
    return ThreadImpact.objects.create(
        thread=thread,
        item=item,
        item_hash=expected_hash,
        author=user,
        **fields,
    )
