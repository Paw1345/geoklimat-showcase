import json
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.test import TestCase
from django.utils import timezone

from ingestion.epic45_queue import operational_queue
from ingestion.models import Source


class OperationalQueueTests(TestCase):
    def source(self, slug, **kwargs):
        return Source.objects.create(
            slug=slug, name="Public source", url="https://example.com/", **kwargs
        )

    def test_verified_disabled_is_not_enabled_or_accepted(self):
        self.source("disabled", enabled=False, last_audit={"status": "verified"})
        row = operational_queue()["channel_rows"][0]
        self.assertFalse(row["enabled"])
        self.assertFalse(row["disabled_reason_confirmed"])
        self.assertIn("why verified", row["next_step"])
        self.assertFalse(Source.objects.get(slug="disabled").enabled)

    def test_freshness_boundary_failed_channel_and_healthy_exclusion(self):
        now = timezone.now()
        self.source("boundary", enabled=True, last_success_at=now - timedelta(days=1))
        self.source("stale", enabled=True, last_success_at=now - timedelta(days=1, seconds=1))
        self.source("failed", enabled=True, last_success_at=now, failures=1)
        report = operational_queue(now)
        self.assertEqual([r["slug"] for r in report["channel_rows"]], ["failed", "stale"])
        self.assertEqual(report["enabled_problem_count"], 2)

    def test_unknown_error_and_config_are_not_exposed(self):
        self.source(
            "safe",
            config={"private": "fixture-private-value"},
            last_audit={"status": "unexpected-fixture", "error": "fixture-sensitive-error"},
        )
        report = operational_queue()
        raw = json.dumps(report, cls=DjangoJSONEncoder)
        self.assertNotIn("fixture-sensitive-error", raw)
        self.assertNotIn("fixture-private-value", raw)
        self.assertEqual(report["channel_rows"][0]["audit_status_recorded"], "unknown")

    def test_hint_is_historical_not_proof_of_disabled_reason(self):
        self.source(
            "policy",
            last_audit={
                "status": "unverified",
                "error": "Cannot confirm robots policy",
            },
        )
        report = operational_queue()
        self.assertEqual(report["channel_rows"][0]["audit_problem_hint"], "robots_unconfirmed")
        self.assertFalse(report["channel_rows"][0]["disabled_reason_confirmed"])
        self.assertFalse(report["automatic_activation"])

    def test_enabled_without_success_requires_review(self):
        self.source("never", enabled=True)
        report = operational_queue()
        self.assertEqual(report["enabled_problem_count"], 1)
        self.assertTrue(report["channel_rows"][0]["never_succeeded"])
