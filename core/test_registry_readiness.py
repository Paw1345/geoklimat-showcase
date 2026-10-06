import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.registry_readiness import report
from ingestion.models import (
    DataChannel,
    DataHarvest,
    DownloadJob,
    IngestionRun,
    RegistryEntry,
    RegistryRelease,
    Source,
)
from ingestion.source_policy import POLICY_ERROR


class ReadinessTests(TestCase):
    def test_changed_policy_is_attention_flag_not_healthy_channel(self):
        IngestionRun.objects.create(source=self.source, status="failed", error=POLICY_ERROR)
        row = self.row()
        self.assertEqual(row["sources"][0]["state"], "policy_changed")
        self.assertTrue(row["attention"])
        self.assertEqual(self.source.failures, 0)

    def test_cli_uses_same_filters_without_http_or_writes(self):
        stream = io.StringIO()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command(
                "registry_readiness", format="json", query="BDPN", priority="P1", stdout=stream
            )
        result = json.loads(stream.getvalue())
        self.assertEqual(result["filtered_total"], 1)
        self.assertFalse(result["business_signoff_included"])
        self.assertEqual(DownloadJob.objects.count(), 0)
        with patch(
            "ingestion.management.commands.registry_readiness.report",
            side_effect=AssertionError("No query"),
        ):
            with self.assertRaises(CommandError):
                call_command("registry_readiness", priority="BAD", stdout=io.StringIO())

    def test_query_count_constant_with_additional_registry_row(self):
        RegistryEntry.objects.create(
            registry_id=151,
            release=self.entry.release,
            organization="OPN",
            name="Other",
            priority="P0",
            technical_status="VERIFY_ENDPOINT",
            integration_state="needs_endpoint",
            master={},
            technical={},
        ).sources.add(self.source)
        with self.assertNumQueries(3):
            result = report(self.now)
        self.assertEqual(result["registry_total"], 2)
        self.assertEqual(result["source_total"], 1)

    def setUp(self):
        self.now = timezone.now()
        self.staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=144,
            release=release,
            organization="BDPN",
            name="Park",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-144",
            name="News",
            adapter="web",
            url="https://bdpn.gov.pl/aktualnosci",
            enabled=True,
            activation_approved=True,
            last_success_at=self.now,
            interval_minutes=360,
            scope="Pierwsza strona, nie pełny BIP",
            config={"token": "DO_NOT_EXPORT"},
        )
        self.entry.sources.add(self.source)
        self.channel = DataChannel.objects.create(
            registry=self.entry,
            slug="file",
            name="File",
            kind="file",
            region="PL",
            url="https://bdpn.gov.pl/file.pdf",
            enabled=False,
            access_approved=True,
            config={"secret": "DO_NOT_EXPORT"},
        )

    def row(self):
        return report(self.now)["rows"][0]

    def test_report_no_network_no_writes_and_three_queries(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            with self.assertNumQueries(3):
                result = report(self.now)
        self.assertEqual((result["registry_total"], result["attention_total"]), (1, 0))
        self.assertFalse(result["business_signoff_included"])
        self.assertFalse(result["rows"][0]["business_coverage_verified"])
        self.assertEqual(self.row()["data"][0]["state"], "on_demand")
        self.assertEqual(DownloadJob.objects.count(), 0)
        self.assertNotIn("DO_NOT_EXPORT", str(result))

    def test_operational_success_not_business_signoff(self):
        self.entry.integration_state = "needs_connector"
        self.entry.save()
        row = self.row()
        self.assertTrue(row["attention"])
        self.assertEqual(row["sources"][0]["state"], "recent")
        self.assertFalse(row["business_coverage_verified"])

    def test_missing_relations_and_unapproved_not_silently_ready(self):
        self.entry.sources.clear()
        self.channel.delete()
        row = self.row()
        self.assertTrue(row["attention"])
        self.assertIn("Brak powiązanego kanału", row["reasons"])

    def test_source_stale_failures_disabled_and_approval(self):
        for changes, state in [
            ({"last_success_at": self.now - timedelta(days=2)}, "stale"),
            ({"failures": 1}, "failed"),
            ({"enabled": False}, "disabled"),
            ({"activation_approved": False}, "unapproved"),
        ]:
            old = {k: getattr(self.source, k) for k in changes}
            Source.objects.filter(pk=self.source.pk).update(**changes)
            row = self.row()
            self.assertTrue(row["attention"])
            self.assertEqual(row["sources"][0]["state"], state)
            Source.objects.filter(pk=self.source.pk).update(**old)

    def test_data_stored_and_retention_not_guaranteed_by_complete(self):
        self.channel.kind = "arcgis"
        self.channel.enabled = True
        self.channel.last_status = "stored"
        self.channel.checked_at = self.now
        self.channel.save()
        DataHarvest.objects.create(
            channel=self.channel,
            config_hash="a" * 64,
            status="complete",
            start_url=self.channel.url,
            next_url="",
            records=100,
        )
        row = self.row()
        self.assertEqual(row["data"][0]["state"], "recent")
        self.assertEqual(row["data"][0]["harvest_records"], 100)
        self.assertFalse(row["business_coverage_verified"])
        self.channel.checked_at = self.now - timedelta(days=2)
        self.channel.save()
        self.assertEqual(self.row()["data"][0]["state"], "stale")

    def test_latest_download_retry_requires_review(self):
        DownloadJob.objects.create(
            channel=self.channel,
            url=self.channel.url,
            config_hash="a" * 64,
            expires_at=self.now + timedelta(days=1),
            status="complete",
        )
        DownloadJob.objects.create(
            channel=self.channel,
            url=self.channel.url,
            config_hash="b" * 64,
            expires_at=self.now + timedelta(days=1),
            status="retry",
        )
        row = self.row()
        self.assertEqual(row["data"][0]["download_status"], "retry")
        self.assertTrue(row["attention"])

    def test_permissions_get_only_no_cache_and_filtered_json(self):
        url = "/registry/readiness/"
        self.assertEqual(self.client.get(url).status_code, 302)
        reader = get_user_model().objects.create_user(username="reader")
        self.client.force_login(reader)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(url).status_code, 405)
        response = self.client.get(url, {"format": "json", "q": "BDPN", "priority": "P1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.json()["filtered_total"], 1)
        self.assertNotContains(response, "DO_NOT_EXPORT")
        self.assertEqual(
            self.client.get(url, {"format": "json", "attention": "yes"}).json()["filtered_total"], 0
        )
        self.assertEqual(
            self.client.get(url, {"format": "json", "q": "unknown"}).json()["filtered_total"], 0
        )

    def test_invalid_filters_fail_without_report(self):
        self.client.force_login(self.staff)
        with patch("core.registry_readiness.report", side_effect=AssertionError("No query")):
            for params in [
                {"priority": "P999"},
                {"attention": "maybe"},
                {"format": "csv"},
                {"q": "x" * 201},
            ]:
                self.assertEqual(self.client.get("/registry/readiness/", params).status_code, 400)

    def test_html_escapes_scope_and_explains_no_business_acceptance(self):
        self.client.force_login(self.staff)
        self.source.scope = "<script>alert(1)</script>"
        self.source.save()
        response = self.client.get("/registry/readiness/")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "nie potwierdza pełnego pokrycia biznesowego")
