from django.conf import settings
from django.db import models
from django.utils import timezone


class AIDraft(models.Model):
    thread = models.ForeignKey("ChangeThread", on_delete=models.PROTECT, related_name="ai_drafts")
    item = models.ForeignKey("Item", on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    state = models.CharField(max_length=12, default="queued")
    input_data = models.JSONField()
    input_hash = models.CharField(max_length=64)
    item_hash = models.CharField(max_length=64)
    model_name = models.CharField(max_length=100, default="qwen3:4b")
    # Historical fallback; enqueue/run_one explicitly record the actual prompt version.
    prompt_version = models.CharField(max_length=30, default="geoklimat-local-v1")
    output = models.JSONField(default=dict)
    error = models.CharField(max_length=120, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.PROTECT,
        related_name="reviewed_ai_drafts",
    )
    reviewed_at = models.DateTimeField(null=True)
    impact = models.ForeignKey("ThreadImpact", null=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["thread", "item"],
                condition=models.Q(state__in=["queued", "running"]),
                name="ai_one_pending_document",
            )
        ]
