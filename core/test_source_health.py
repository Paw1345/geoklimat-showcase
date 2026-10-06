from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from core.quality import report
from ingestion.models import IngestionRun, RegistryEntry, RegistryRelease, Source
from ingestion.source_health import is_stale, source_health, with_run_summary
from ingestion.source_policy import POLICY_ERROR


class SourceHealthTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.source = Source.objects.create(
            slug="health-test",
            name="Test",
            url="https://www.gov.pl/test",
            adapter="watch",
            enabled=True,
            activation_approved=True,
            last_success_at=self.now,
            interval_minutes=60,
        )
        release = RegistryRelease.objects.create(filename="test", sha256="f" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=168,
            release=release,
            master={},
            technical={},
            organization="Test",
            name="Procedura",
            integration_state="linked",
        )
        self.entry.sources.add(self.source)

    def test_activation_states_take_precedence(self):
        for approved, enabled, expected in [(False, True, "unapproved"), (True, False, "disabled")]:
            with self.subTest(expected=expected):
                self.source.activation_approved = approved
                self.source.enabled = enabled
                self.source.failures = 3
                self.assertEqual(source_health(self.source, self.now)["state"], expected)

    def test_policy_change_requires_review_without_fabricated_provider_failure(self):
        IngestionRun.objects.create(source=self.source, status="failed", error=POLICY_ERROR)
        current = with_run_summary(Source.objects.all()).get(pk=self.source.pk)
        health = source_health(current, self.now)
        self.assertEqual(health["state"], "policy_changed")
        self.assertEqual(health["label"], "Import zatrzymany")
        self.assertIn("nie oznacza to awarii dostawcy", health["detail"])
        self.assertEqual(current.failures, 0)
        current.enabled = False
        self.assertEqual(source_health(current, self.now)["state"], "disabled")
        current.activation_approved = False
        self.assertEqual(source_health(current, self.now)["state"], "unapproved")

    def test_pending_and_freshness_boundary(self):
        self.source.last_success_at = None
        self.assertEqual(source_health(self.source, self.now)["state"], "pending")
        self.assertTrue(is_stale(self.source, self.now))
        for interval, age, expected in [
            (60, 1440, "recent"),
            (60, 1441, "stale"),
            (1440, 4320, "recent"),
            (1440, 4321, "stale"),
        ]:
            with self.subTest(interval=interval, age=age):
                self.source.interval_minutes = interval
                self.source.last_success_at = self.now - timedelta(minutes=age)
                self.assertEqual(source_health(self.source, self.now)["state"], expected)

    def test_failure_partial_and_recovery(self):
        self.source.failures = 1
        self.source.latest_run_status = "failed"
        self.source.latest_run_error = "Cannot confirm robots policy"
        health = source_health(self.source, self.now)
        self.assertEqual(health["state"], "failed")
        self.assertIn("robots.txt", health["detail"])
        self.source.failures = 0
        self.source.latest_run_status = "partial"
        self.assertEqual(source_health(self.source, self.now)["state"], "partial")
        for status in ("success", "not_modified"):
            self.source.latest_run_status = status
            self.assertEqual(source_health(self.source, self.now)["state"], "recent")

    def test_page_failure_is_read_only_and_does_not_expose_exceptions(self):
        self.source.failures = 15
        self.source.last_audit = {"status": "verified", "error": "private-audit-detail"}
        self.source.save()
        IngestionRun.objects.create(source=self.source, status="failed", error="private-run-detail")
        with patch("ingestion.transport.Client.get") as get, patch.object(Source, "save") as save:
            response = self.client.get("/registry/168/")
        get.assert_not_called()
        save.assert_not_called()
        self.assertContains(response, "Błąd pobierania")
        self.assertContains(response, "Kolejne błędy pobierania: 15")
        self.assertNotContains(response, "private-run-detail")
        self.assertNotContains(response, "private-audit-detail")
        self.assertNotContains(response, "Zaakceptowany wyjątek odbioru")
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.integration_state, "linked")
        self.assertEqual(self.client.post("/registry/168/").status_code, 405)

    def test_newest_run_tie_and_fixed_query_count(self):
        IngestionRun.objects.create(
            source=self.source,
            started_at=self.now,
            status="failed",
            error="Cannot confirm robots policy",
        )
        IngestionRun.objects.create(source=self.source, started_at=self.now, status="not_modified")
        with self.assertNumQueries(1):
            source = with_run_summary(Source.objects.all()).get(pk=self.source.pk)
        self.assertEqual(source.latest_run_status, "not_modified")
        self.assertEqual(source.latest_run_error, "")
        with self.assertNumQueries(2):
            response = self.client.get("/registry/168/")
        self.assertContains(response, "Ostatnie pobranie poprawne")

    def test_quality_report_and_card_share_stale_definition(self):
        self.source.last_success_at = self.now - timedelta(days=2)
        self.source.save()
        with patch("core.quality.timezone.now", return_value=self.now):
            self.assertEqual(report()["sources_stale"], 1)
        self.assertContains(self.client.get("/registry/168/"), "Nieaktualne pobieranie")

    def test_robots_error_is_safe_and_visible(self):
        self.source.failures = 1
        self.source.save()
        IngestionRun.objects.create(
            source=self.source, status="failed", error="Cannot confirm robots policy"
        )
        response = self.client.get("/registry/168/")
        self.assertContains(response, "Nie potwierdzono polityki robots.txt")
        self.assertContains(response, "pobieranie zablokowane")
