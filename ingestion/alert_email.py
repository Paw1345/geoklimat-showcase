"""E-mail copy of a user's alert digest. Off unless ALERT_EMAIL_ENABLED is set.

The in-app digest stays the source of truth. A digest is mailed at most once: the
row is locked, the message is sent and the send time is stored in one transaction.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from ingestion.models import Notification

logger = logging.getLogger(__name__)

MAX_AGE = timedelta(days=3)
BATCH = 50
LISTED = 20


def message(digest):
    entries = list(digest.entries.filter(user_id=digest.user_id).order_by("created_at")[:LISTED])
    total = digest.payload.get("count", len(entries))
    lines = [f"{digest.title}: {total} zmian w obserwowanych wątkach.", ""]
    lines += [f"- {entry.title}" for entry in entries]
    if total > len(entries):
        lines.append(f"- i {total - len(entries)} kolejnych")
    if settings.SITE_URL:
        path = reverse("alert_detail", args=[digest.pk])
        lines += ["", f"Szczegóły: {settings.SITE_URL.rstrip('/')}{path}"]
    lines += ["", "Częstotliwość zmienisz w aplikacji: Alerty, Ustawienia."]
    return f"GeoKlimat: {digest.title.lower()} ({total})", "\n".join(lines)


def pending(now):
    return (
        Notification.objects.filter(
            kind="digest",
            created_at__gte=now - MAX_AGE,
            user__is_active=True,
            payload__emailed_at__isnull=True,
        )
        .exclude(user__email="")
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:BATCH]
    )


def send_pending(now=None):
    """Mail unsent digests. Returns the number of messages handed to the mail backend."""
    if not settings.ALERT_EMAIL_ENABLED:
        return 0
    now = now or timezone.now()
    sent = 0
    for pk in list(pending(now)):
        try:
            with transaction.atomic():
                digest = (
                    Notification.objects.select_for_update(of=("self",))
                    .select_related("user")
                    .get(pk=pk)
                )
                if digest.payload.get("emailed_at"):
                    continue
                subject, body = message(digest)
                send_mail(subject, body, None, [digest.user.email])
                digest.payload["emailed_at"] = now.isoformat()
                digest.save(update_fields=["payload"])
                sent += 1
        except Exception as exc:
            # Addresses and server replies stay out of the log.
            logger.error("Alert e-mail failed for digest %s: %s", pk, type(exc).__name__)
    return sent
