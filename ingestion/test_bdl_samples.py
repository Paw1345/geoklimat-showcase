import hashlib
import io
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion.bdl_probe import LICENSE, plan
from ingestion.bdl_samples import ATTRIBUTION, RESERVE, config, run_sample, slug
from ingestion.data_service import run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError

RAW = b'{ "type": "FeatureCollection", "features": [] }\n'
REPORT = {
    "number_returned": 0,
    "declared_api_license": LICENSE,
    "database_writes": False,
    "automatic_collection_enabled": False,
}


class BdlSampleTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="bdlraw", sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=39,
            release=release,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        self.channel = DataChannel.objects.create(
            registry=self.registry,
            slug=slug("rdlp"),
            name="RDLP",
            kind="geojson",
            region="PL",
            url=plan("rdlp")["url"],
            config=config("rdlp"),
            attribution=ATTRIBUTION,
            interval_minutes=1440,
            enabled=True,
            access_approved=True,
        )
        self.fetch = patch("ingestion.bdl_samples.fetch_sample", return_value=(RAW, REPORT)).start()
        self.addCleanup(patch.stopall)

    def test_original_bytes_provenance_retention_and_daily_no_http(self):
        result = run_sample(self.channel, Mock())
        snapshot = DataSnapshot.objects.get(pk=result["snapshot_id"])
        self.assertEqual(bytes(snapshot.payload), RAW)
        self.assertEqual(snapshot.sha256, hashlib.sha256(RAW).hexdigest())
        self.assertEqual(snapshot.size_bytes, len(RAW))
        self.assertEqual(snapshot.expires_at - snapshot.fetched_at, timedelta(days=30))
        self.assertTrue(snapshot.summary["original_response_preserved"])
        self.assertTrue(snapshot.summary["database_writes"])
        self.assertEqual(snapshot.summary["acquired_at"], snapshot.fetched_at.isoformat())
        self.assertIsNone(snapshot.summary["source_publication_time"])
        self.assertEqual(run_sample(self.channel, Mock())["status"], "daily_limit")
        self.fetch.assert_called_once()
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_dry_run_no_snapshot_or_schedule_write(self):
        before = self.channel.next_fetch_at
        self.assertEqual(run_sample(self.channel, Mock(), True)["status"], "dry_run")
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.next_fetch_at, before)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    @override_settings(DATA_STORAGE_MAX_BYTES=RESERVE)
    def test_full_cache_refuses_before_get(self):
        with self.assertRaises(FetchError):
            run_sample(self.channel, Mock())
        self.fetch.assert_not_called()
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_post_get_cache_growth_rolls_back_copy_and_schedule(self):
        before = self.channel.next_fetch_at
        with patch("ingestion.bdl_samples.capacity", side_effect=[None, FetchError("full")]):
            with self.assertRaises(FetchError):
                run_sample(self.channel, Mock())
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.next_fetch_at, before)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_post_get_revocation_is_not_overwritten_by_run_data_error(self):
        def revoke(*args):
            DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
            return RAW, REPORT

        self.fetch.side_effect = revoke
        result = run_data(self.channel.slug)
        self.assertEqual(result[0]["status"], "failed")
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.access_approved)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_disabled_and_modified_profiles_refuse(self):
        self.channel.enabled = False
        with self.assertRaises(FetchError):
            run_sample(self.channel, Mock())
        self.channel.enabled = True
        self.channel.config = {**self.channel.config, "offset": 1}
        with self.assertRaises(FetchError):
            run_sample(self.channel, Mock())
        self.fetch.assert_not_called()

    def test_next_utc_day_can_store_new_original_copy(self):
        run_sample(self.channel, Mock())
        DataSnapshot.objects.update(fetched_at=timezone.now() - timedelta(days=1))
        self.assertEqual(run_sample(self.channel, Mock())["status"], "stored")
        self.assertEqual(DataSnapshot.objects.count(), 2)

    def test_configure_dry_atomic_admin_guard_apply_repeat(self):
        self.channel.delete()
        call_command("configure_bdl_samples", stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 0)
        call_command("configure_bdl_samples", apply=True, stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 3)
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())
        self.assertFalse(DataChannel.objects.filter(access_approved=True).exists())
        call_command("configure_bdl_samples", apply=True, stdout=io.StringIO())
        DataChannel.objects.filter(slug=slug("lesnictwa")).update(config={"admin": True})
        with self.assertRaises(CommandError):
            call_command("configure_bdl_samples", apply=True, stdout=io.StringIO())
        self.registry.refresh_from_db()
        self.assertEqual(self.registry.integration_state, "needs_connector")
