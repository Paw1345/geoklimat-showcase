import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError
from django.test import TransactionTestCase
from django.utils import timezone

from core.stac import report
from ingestion.data_paging import digest, initial_url
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.stac_coverage import coverage


class StacCoverageTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        release = RegistryRelease.objects.create(filename="fixture", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=191, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="fixture-stac",
            name="Fixture",
            kind="stac",
            region="PL",
            url="https://stac.dataspace.copernicus.eu/v1/search",
            config={
                "pagination": "stac_get",
                "bbox": [14.12, 49.0, 24.15, 54.84],
                "collection": "sentinel-2-l2a",
            },
        )
        with patch("ingestion.data_paging.timezone.now", return_value=self.now):
            self.start_url = initial_url(self.channel)
        self.harvest = DataHarvest.objects.create(
            channel=self.channel,
            config_hash=digest(
                [self.channel.url, self.channel.kind, self.channel.region, self.channel.config]
            ),
            start_url=self.start_url,
            next_url="",
            status="complete",
            pages=1,
            records=2,
        )

    def copy(self, **changes):
        summary = {
            "kind": "stac",
            "scope": "PL",
            "bbox": self.channel.config["bbox"],
            "harvest_id": self.harvest.pk,
            "page": 1,
            "records": 2,
            "raw_records": 2,
            "request_url": self.start_url,
            "assets_downloaded": False,
            "oldest_observation": (self.now - timedelta(days=2)).isoformat(),
            "newest_observation": (self.now - timedelta(days=1)).isoformat(),
        }
        summary.update(changes)
        return DataSnapshot.objects.create(
            channel=self.channel,
            fetched_at=self.now,
            expires_at=self.now + timedelta(days=30),
            size_bytes=4,
            payload=b"abcd",
            sha256="a" * 64,
            summary=summary,
            media_type="application/json",
        )

    def test_window_calendar_is_metadata_only_without_fake_observation_counts(self):
        self.copy()
        with patch("ingestion.data_paging.timezone.now", return_value=self.now - timedelta(days=2)):
            self.harvest.start_url = initial_url(self.channel)
        with patch.object(DataSnapshot.objects, "only", side_effect=AssertionError("No payload")):
            result = coverage(self.channel, self.harvest, self.now)
        calendar = result["window_timeline"]
        self.assertFalse(calendar["observations_available_for_entire_manifest"])
        self.assertGreaterEqual(calendar["days_outside_frozen_search"], 1)
        self.assertTrue(
            all(row["record_occurrences_in_manifest"] is None for row in calendar["days"])
        )
        self.assertFalse(result["full_current_30_day_coverage_verified"])

    def test_complete_metadata_is_not_integrity_unique_scenes_or_current_history(self):
        self.copy()
        result = coverage(self.channel, self.harvest, self.now)
        self.assertTrue(result["scan_complete"])
        self.assertTrue(result["all_read_pages_present"])
        self.assertEqual(result["available_record_occurrences"], 2)
        self.assertEqual(result["search_end"], self.now)
        for key in (
            "payload_hashes_verified",
            "unique_scenes_verified",
            "geographic_mask_verified",
            "full_current_30_day_coverage_verified",
        ):
            self.assertFalse(result[key])

    def test_running_frozen_window_and_missing_pages(self):
        self.copy()
        self.harvest.pages = 2
        self.harvest.status = "running"
        self.harvest.next_url = self.start_url + "&token=private-cursor"
        result = coverage(self.channel, self.harvest, self.now + timedelta(hours=3))
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["unavailable_pages"], 1)
        self.assertEqual(result["missing_snapshot_pages"], 1)
        self.assertEqual(result["missing_page_numbers_sample"], [2])
        self.assertEqual(result["search_end_lag_seconds"], 10800)
        self.assertNotIn("private-cursor", json.dumps(result, default=str))

    def test_expiry_and_observation_boundary_fail_closed_without_deleting(self):
        snapshot = self.copy(oldest_observation=(self.now - timedelta(days=30)).isoformat())
        result = coverage(self.channel, self.harvest, self.now)
        self.assertEqual(result["available_pages"], 0)
        self.assertIn("observation_retention_review", result["issues"])
        snapshot.summary["oldest_observation"] = (self.now - timedelta(days=2)).isoformat()
        snapshot.expires_at = self.now
        snapshot.save()
        result = coverage(self.channel, self.harvest, self.now)
        self.assertEqual(result["unavailable_pages"], 1)
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_duplicate_and_invalid_metadata(self):
        self.copy()
        self.copy()
        result = coverage(self.channel, self.harvest, self.now)
        self.assertIn("invalid_page_metadata", result["issues"])
        self.assertFalse(result["all_read_pages_present"])
        self.assertEqual(result["available_pages"], 1)
        self.assertEqual(result["stored_snapshot_rows"], 2)
        self.assertEqual(result["rejected_snapshot_rows"], 1)

    def test_scope_changes_dates_and_page_urls_rejected(self):
        self.copy(request_url=self.start_url.replace("limit=10", "limit=100"))
        self.assertIn(
            "page_scope_mismatch", coverage(self.channel, self.harvest, self.now)["issues"]
        )
        self.harvest.start_url += "&datetime=invalid"
        self.assertEqual(
            coverage(self.channel, self.harvest, self.now)["issues"], ["scope_or_profile_mismatch"]
        )
        self.harvest.start_url = self.start_url
        self.channel.config["bbox"] = [0, 0, 1, 1]
        self.assertEqual(
            coverage(self.channel, self.harvest, self.now)["issues"], ["scope_or_profile_mismatch"]
        )

    def test_no_harvest_and_no_exposed_urls_or_cursors(self):
        self.assertEqual(coverage(self.channel, None, self.now)["status"], "not_started")
        self.copy()
        result = report()
        self.assertNotIn("dataspace", json.dumps(result, default=str))
        self.assertNotIn("request_url", json.dumps(result, default=str))

    def test_cli_http_and_writes_absent(self):
        self.copy()
        stream = io.StringIO()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command("stac_coverage", stdout=stream)
        self.assertTrue(json.loads(stream.getvalue())["read_only"])
        self.assertEqual(DataSnapshot.objects.count(), 1)
        self.harvest.refresh_from_db()
        self.assertEqual(self.harvest.status, "complete")

    def test_staff_get_json_no_store_and_html_escape(self):
        self.assertEqual(self.client.get("/data/stac/").status_code, 302)
        staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_login(staff)
        self.channel.name = '<script>alert("fixture")</script>'
        self.channel.save(update_fields=["name"])
        response = self.client.get("/data/stac/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("fixture")</script>')
        response = self.client.get("/data/stac/?format=json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.post("/data/stac/").status_code, 405)
        self.assertEqual(self.client.get("/data/stac/?format=csv").status_code, 400)

    def test_malformed_profile_and_naive_observation_bounds(self):
        self.copy(newest_observation="2026-10-01T12:00:00")
        result = coverage(self.channel, self.harvest, self.now)
        self.assertIn("observation_bounds_invalid", result["issues"])
        self.assertEqual(result["available_pages"], 0)
        self.channel.config = []
        self.assertEqual(
            coverage(self.channel, self.harvest, self.now)["issues"],
            ["scope_or_profile_mismatch"],
        )

    def test_hard_expiry_and_cursor_status_consistency(self):
        snapshot = self.copy()
        snapshot.fetched_at = self.now - timedelta(days=30)
        snapshot.save(update_fields=["fetched_at"])
        self.assertEqual(coverage(self.channel, self.harvest, self.now)["available_pages"], 0)
        self.harvest.next_url = self.start_url
        result = coverage(self.channel, self.harvest, self.now)
        self.assertFalse(result["scan_complete"])
        self.assertIn("cursor_status_mismatch", result["issues"])

    def test_report_transaction_refuses_writes(self):
        def attempt_write(*args, **kwargs):
            DataChannel.objects.all().update(name="mutated")

        with patch.object(DataChannel.objects, "filter", side_effect=attempt_write):
            with self.assertRaises(DatabaseError):
                report()
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.name, "Fixture")
