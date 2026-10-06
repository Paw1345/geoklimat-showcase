"""Change threads, editorial evidence revisions and private follows."""

from django.conf import settings
from django.db import models
from django.utils import timezone


class ChangeThread(models.Model):
    process = models.OneToOneField(
        "LegalProcess",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="change_thread",
    )
    title = models.CharField(max_length=1000)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        permissions = [("edit_change_threads", "Edit change threads and evidence")]

    @property
    def display_title(self):
        return self.process.title if self.process_id else self.title


class ThreadDocument(models.Model):
    thread = models.ForeignKey(ChangeThread, on_delete=models.PROTECT, related_name="decisions")
    item = models.ForeignKey("Item", on_delete=models.PROTECT)
    included = models.BooleanField()
    reason = models.TextField()
    item_hash = models.CharField(max_length=64)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    superseded_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "item"],
                condition=models.Q(superseded_at__isnull=True),
                name="thread_current_document",
            )
        ]


class ThreadImpact(models.Model):
    KINDS = [
        ("legal", "Prawny"),
        ("technical", "Techniczny"),
        ("organizational", "Organizacyjny"),
        ("financial", "Finansowy"),
    ]
    thread = models.ForeignKey(ChangeThread, on_delete=models.PROTECT, related_name="impacts")
    item = models.ForeignKey("Item", on_delete=models.PROTECT)
    item_hash = models.CharField(max_length=64)
    change = models.TextField()
    audience = models.TextField()
    kinds = models.JSONField()
    evidence_url = models.URLField(max_length=1500)
    evidence = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    superseded_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["thread"],
                condition=models.Q(superseded_at__isnull=True),
                name="thread_current_impact",
            )
        ]

    @property
    def kind_labels(self):
        return [dict(self.KINDS).get(kind, kind) for kind in self.kinds]


class ThreadWatch(models.Model):
    alert_state = models.JSONField(null=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    thread = models.ForeignKey(ChangeThread, on_delete=models.PROTECT, related_name="watches")
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "thread"], name="thread_watch_owner")
        ]
