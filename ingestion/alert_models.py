"""Private in-app notifications; no external delivery without a configured channel."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class AlertPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=True)
    cadence = models.CharField(
        max_length=10,
        default="daily",
        choices=[("none", "Bez podsumowań"), ("daily", "Codziennie"), ("weekly", "Co tydzień")],
    )
    digest_since = models.DateTimeField(default=timezone.now)


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    thread = models.ForeignKey("ChangeThread", null=True, on_delete=models.PROTECT)
    kind = models.CharField(
        max_length=10, choices=[("change", "Zmiana"), ("digest", "Podsumowanie")]
    )
    title = models.CharField(max_length=1000)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True)
    digest = models.ForeignKey("self", null=True, on_delete=models.SET_NULL, related_name="entries")

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["user", "read_at", "created_at"], name="alert_owner_unread")
        ]
