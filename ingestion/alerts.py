"""Transactional, repeat-safe snapshot alerts and rolling local digests."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ingestion.changes import thread_items
from ingestion.legislation import current_events
from ingestion.models import AlertPreference, LegalEvent, Notification, ThreadWatch


def snapshot(thread):
    items = dict(thread_items(thread).values_list("pk", "content_hash"))
    impact = thread.impacts.filter(superseded_at__isnull=True).first()
    events = current_events(
        LegalEvent.objects.filter(
            link__item_id__in=items,
            kind__in=["effective", "consultation_end", "reporting_deadline"],
        )
    )
    return {
        "items": {str(pk): value for pk, value in items.items()},
        "impact": [impact.pk, items.get(impact.item_id) == impact.item_hash] if impact else None,
        "events": {str(pk): date.isoformat() for pk, date in events.values_list("pk", "date")},
    }


def difference(old, new):
    old_items, new_items = old["items"], new["items"]
    added = sorted(set(new_items) - set(old_items), key=int)
    removed = sorted(set(old_items) - set(new_items), key=int)
    changed = sorted(
        (pk for pk in set(old_items) & set(new_items) if old_items[pk] != new_items[pk]), key=int
    )
    result = {
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
        "items": [int(pk) for pk in (added + changed + removed)[:50]],
        "impact": old["impact"] != new["impact"],
        "deadlines": old["events"] != new["events"],
    }
    return (
        result
        if any(result[key] for key in ("added", "removed", "changed", "impact", "deadlines"))
        else None
    )


@transaction.atomic
def set_watch(user, thread, active):
    # Serialize against collector and preference changes using the same lock order.
    pref, _ = AlertPreference.objects.get_or_create(user=user)
    AlertPreference.objects.select_for_update().get(pk=pref.pk)
    watch, created = ThreadWatch.objects.get_or_create(user=user, thread=thread)
    watch = ThreadWatch.objects.select_for_update().get(pk=watch.pk)
    if created or watch.active != active:
        watch.active = active
        watch.updated_at = timezone.now()
        watch.alert_state = snapshot(thread) if active else None
        watch.save()
    return watch


@transaction.atomic
def configure(user, enabled, cadence):
    if cadence not in {"none", "daily", "weekly"}:
        raise ValueError("Invalid cadence")
    pref, _ = AlertPreference.objects.get_or_create(user=user)
    pref = AlertPreference.objects.select_for_update().get(pk=pref.pk)
    if pref.enabled != enabled:
        ThreadWatch.objects.filter(user=user).update(alert_state=None)
    if pref.enabled != enabled or pref.cadence != cadence:
        pref.digest_since = timezone.now()
    pref.enabled, pref.cadence = enabled, cadence
    pref.save()


@transaction.atomic
def collect_user(user_id, now):
    pref, _ = AlertPreference.objects.get_or_create(user_id=user_id)
    pref = AlertPreference.objects.select_for_update().get(pk=pref.pk)
    if not pref.enabled or not pref.user.is_active:
        return 0
    count = 0
    for watch in (
        ThreadWatch.objects.select_for_update(of=("self",))
        .filter(user_id=user_id, active=True)
        .select_related("thread", "thread__process")
    ):
        current = snapshot(watch.thread)
        change = difference(watch.alert_state, current) if watch.alert_state is not None else None
        if change:
            Notification.objects.create(
                user_id=user_id,
                thread=watch.thread,
                kind="change",
                title=watch.thread.display_title,
                payload=change,
                created_at=now,
            )
            count += 1
        watch.alert_state = current
        watch.save(update_fields=["alert_state"])
    days = {"daily": 1, "weekly": 7}.get(pref.cadence)
    if days and now >= pref.digest_since + timedelta(days=days):
        entries = Notification.objects.filter(
            user_id=user_id,
            kind="change",
            digest__isnull=True,
            created_at__gt=pref.digest_since,
            created_at__lte=now,
        )
        total = entries.count()
        if total:
            digest = Notification.objects.create(
                user_id=user_id,
                kind="digest",
                title="Podsumowanie dzienne" if days == 1 else "Podsumowanie tygodniowe",
                payload={
                    "count": total,
                    "since": pref.digest_since.isoformat(),
                    "until": now.isoformat(),
                },
                created_at=now,
            )
            entries.update(digest=digest)
            count += 1
        pref.digest_since = now
        pref.save(update_fields=["digest_since"])
    return count


def collect():
    now = timezone.now()
    owners = (
        ThreadWatch.objects.filter(active=True, user__is_active=True)
        .order_by()
        .values_list("user_id", flat=True)
        .distinct()
    )
    # Also finish a pending digest after the last thread has been unfollowed.
    owners = set(owners) | set(
        AlertPreference.objects.filter(enabled=True, user__is_active=True).values_list(
            "user_id", flat=True
        )
    )
    return sum(collect_user(owner, now) for owner in sorted(owners))
