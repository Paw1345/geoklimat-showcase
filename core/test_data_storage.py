import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.data_storage import report
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease


class StorageTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        release = RegistryRelease.objects.create(filename="fixture", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="fixture",
            name="Fixture",
            kind="imgw",
            region="PL",
            url="https://example.org/",
            config={"secret": "never-export-this"},
            last_error="private-error-never-export",
        )
        self.staff = get_user_model().objects.create_user(username="staff", is_staff=True)

    def copy(self, **kwargs):
        values = {
            "channel": self.channel,
            "payload": b"abcd",
            "size_bytes": 4,
            "sha256": "a" * 64,
            "media_type": "application/json",
            "fetched_at": self.now,
            "expires_at": self.now + timedelta(days=30),
        }
        values.update(kwargs)
        return DataSnapshot.objects.create(**values)

    def test_empty_and_no_coverage_claims(self):
        result = report(self.now)
        self.assertEqual(result["total"]["actual_bytes"], 0)
        self.assertEqual(len(result["channels"]), 1)
        self.assertIsNone(result["channels"][0]["oldest_copy"])
        for key in (
            "observation_coverage_verified",
            "payload_hashes_verified",
            "disk_free_space_measured",
        ):
            self.assertFalse(result[key])

    @override_settings(DATA_STORAGE_MAX_BYTES=10)
    def test_actual_bytes_mismatch_quota_and_expiry_boundary(self):
        self.copy(size_bytes=12)
        self.copy(expires_at=self.now)
        result = report(self.now)
        self.assertEqual(result["total"]["actual_bytes"], 8)
        self.assertEqual(result["total"]["declared_bytes"], 16)
        self.assertEqual(result["total"]["expired_bytes"], 4)
        self.assertEqual(result["total"]["size_mismatches"], 1)
        self.assertEqual(result["remaining_logical_bytes"], 0)
        self.assertEqual(result["over_quota_bytes"], 6)
        self.assertEqual(DataSnapshot.objects.count(), 2)

    def test_hard_expiry_and_whole_job_expiry(self):
        self.copy(fetched_at=self.now - timedelta(days=30))
        job = DownloadJob.objects.create(
            channel=self.channel,
            url="https://example.org/file",
            config_hash="a" * 64,
            expires_at=self.now,
            status="blocked",
        )
        self.copy(download_job=job)
        result = report(self.now)
        self.assertEqual(result["total"]["expired_copies"], 2)
        self.assertEqual(result["total"]["chunk_bytes"], 4)
        self.assertEqual(result["total"]["actual_bytes"], 8)
        self.assertIsNone(result["channels"][0]["newest_copy"])
        job.refresh_from_db()
        self.assertEqual(job.status, "blocked")

    def test_constant_query_scope_and_no_payload_or_private_data_export(self):
        self.copy()
        with CaptureQueriesContext(connection) as queries:
            result = report(self.now)
        selects = [q["sql"] for q in queries if q["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(selects), 2)
        output = json.dumps(result, default=str)
        for secret in ("never-export-this", "private-error-never-export", "abcd", "example.org"):
            self.assertNotIn(secret, output)

    def test_read_only_transaction_refuses_writes(self):
        original = DataChannel.objects.order_by

        def attempt_write(*args, **kwargs):
            DataChannel.objects.filter(pk=self.channel.pk).update(name="mutated")
            return original(*args, **kwargs)

        with patch.object(DataChannel.objects, "order_by", side_effect=attempt_write):
            with self.assertRaises(DatabaseError):
                report(self.now)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.name, "Fixture")

    def test_web_permissions_format_post_and_escaping(self):
        self.assertEqual(self.client.get("/data/storage/").status_code, 302)
        self.client.force_login(self.staff)
        self.channel.name = '<script>alert("fixture")</script>'
        self.channel.save(update_fields=["name"])
        response = self.client.get("/data/storage/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("fixture")</script>')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post("/data/storage/").status_code, 405)
        self.assertEqual(self.client.get("/data/storage/?format=csv").status_code, 400)
        response = self.client.get("/data/storage/?format=json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertFalse(response.json()["observation_coverage_verified"])
        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get("/data/storage/").status_code, 302)

    def test_cli_never_http_or_prune(self):
        self.copy()
        stream = io.StringIO()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command("cache_report", stdout=stream)
        self.assertEqual(json.loads(stream.getvalue())["total"]["copies"], 1)
        self.assertEqual(DataSnapshot.objects.count(), 1)
