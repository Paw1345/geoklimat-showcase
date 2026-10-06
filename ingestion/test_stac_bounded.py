import io
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ingestion import test_stac_recent as fixtures
from ingestion.data_paging import advance_harvest, digest
from ingestion.data_service import query_url, run_data
from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.stac_bounded import RESERVE_BYTES, WINDOW_BYTES, defaults, start_url
from ingestion.stac_coverage import search_scope
from ingestion.stac_integrity import verify_batch
from ingestion.transport import FetchError


class StacBoundedTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacRecentTests.setUp(self)
        self.bounded = DataChannel.objects.create(
            slug=self.channel.slug + "-bounded",
            enabled=True,
            access_approved=True,
            **defaults(self.channel),
        )

    def response(self, *args, **kwargs):
        return fixtures.StacRecentTests.response(self, *args, **kwargs)

    def transport(self, *args, **kwargs):
        return fixtures.StacRecentTests.transport(self, *args, **kwargs)

    def test_resume_same_window_and_preserve_history_and_head(self):
        before = self.channel.harvests.values().get()
        first = advance_harvest(self.bounded, self.transport())
        second = advance_harvest(self.bounded, self.transport())
        self.assertEqual(first["harvest_id"], second["harvest_id"])
        self.assertEqual(second["pages"], 4)
        self.assertEqual(DataSnapshot.objects.filter(channel=self.bounded).count(), 4)
        self.assertEqual(self.channel.harvests.values().get(), before)
        self.assertEqual(self.head.harvests.count(), 0)
        harvest = self.bounded.harvests.first()
        self.assertEqual(search_scope(self.bounded, harvest)["search_window_hours"], 24)
        self.assertTrue(verify_batch(self.bounded, harvest, timezone.now(), 1)["batch_verified"])

    def test_twenty_page_limit_keeps_continuation_and_finishes_bounded(self):
        url = start_url(self.bounded)
        harvest = DataHarvest.objects.create(
            channel=self.bounded,
            config_hash=digest(
                [self.bounded.url, self.bounded.kind, self.bounded.region, self.bounded.config]
            ),
            start_url=url,
            next_url=query_url(url, token=20),
            pages=19,
            records=19,
            size_bytes=100,
        )
        result = advance_harvest(self.bounded, self.transport())
        harvest.refresh_from_db()
        self.assertEqual(result["status"], "bounded")
        self.assertEqual(harvest.pages, 20)
        self.assertTrue(harvest.next_url)
        self.assertIsNotNone(harvest.finished_at)

    @override_settings(DATA_AUTO_MAX_BYTES=WINDOW_BYTES)
    def test_byte_limit_reserves_maximum_page_before_another_get(self):
        client = self.transport()
        result = advance_harvest(self.bounded, client)
        self.assertEqual(result["status"], "bounded")
        self.assertEqual(result["pages"], 1)
        client.get.assert_called_once()

    @override_settings(DATA_STORAGE_MAX_BYTES=RESERVE_BYTES)
    def test_cache_reserve_pauses_without_network_and_preserves_cursor(self):
        client = self.transport()
        result = advance_harvest(self.bounded, client)
        harvest = self.bounded.harvests.first()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(harvest.pages, 0)
        self.assertEqual(harvest.next_url, harvest.start_url)
        client.get.assert_not_called()

    def test_reserve_rechecked_after_network_before_commit(self):
        client = self.transport()
        sums = [{"total": 0}, {"total": 15_000_000_000}]
        with patch.object(DataSnapshot.objects, "aggregate", side_effect=sums):
            result = advance_harvest(self.bounded, client)
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["pages"], 0)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_store_failure_rolls_back_page_and_retains_cursor_for_retry(self):
        with patch("ingestion.data_service.store_snapshot", side_effect=FetchError("quota")):
            with self.assertRaises(FetchError):
                advance_harvest(self.bounded, self.transport())
        harvest = self.bounded.harvests.first()
        self.assertEqual(harvest.status, "failed")
        self.assertEqual(harvest.pages, 0)
        self.assertEqual(harvest.next_url, harvest.start_url)
        self.assertEqual(DataSnapshot.objects.count(), 0)
        result = advance_harvest(self.bounded, self.transport(more=False))
        self.assertEqual(result["harvest_id"], harvest.pk)
        self.assertEqual(result["status"], "complete")

    def test_frozen_window_can_resume_after_ten_minutes_not_reset(self):
        first = advance_harvest(self.bounded, self.transport())
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(minutes=30)):
            result = advance_harvest(self.bounded, self.transport())
        self.assertEqual(result["harvest_id"], first["harvest_id"])
        self.assertEqual(result["pages"], 4)

    def test_second_page_failure_preserves_first_and_resumes_its_cursor(self):
        client = self.transport()
        url = start_url(self.bounded)
        client.get.side_effect = [self.response(url), FetchError("timeout")]
        with self.assertRaises(FetchError):
            advance_harvest(self.bounded, client)
        harvest = self.bounded.harvests.first()
        self.assertEqual(harvest.pages, 1)
        self.assertEqual(DataSnapshot.objects.count(), 1)
        result = advance_harvest(self.bounded, self.transport())
        self.assertEqual(result["harvest_id"], harvest.pk)
        self.assertEqual(result["pages"], 3)

    def test_revoked_approval_during_get_refuses_snapshot_before_commit(self):
        client = self.transport()

        def revoke(url):
            DataChannel.objects.filter(pk=self.bounded.pk).update(access_approved=False)
            return self.response(url)

        client.get.side_effect = revoke
        with self.assertRaises(FetchError):
            advance_harvest(self.bounded, client)
        self.assertEqual(DataSnapshot.objects.count(), 0)
        self.assertEqual(self.bounded.harvests.first().pages, 0)

    def test_expired_window_is_retained_and_new_scan_starts(self):
        first = advance_harvest(self.bounded, self.transport())
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(hours=49)):
            result = advance_harvest(self.bounded, self.transport(more=False))
        old = DataHarvest.objects.get(pk=first["harvest_id"])
        self.assertEqual(old.status, "expired")
        self.assertEqual(old.pages, 2)
        self.assertTrue(old.next_url)
        self.assertNotEqual(result["harvest_id"], old.pk)

    def test_scheduler_collecting_and_terminal_cadence(self):
        with patch("ingestion.data_service.Client", return_value=self.transport()):
            result = run_data(self.bounded.slug, force=True)
        self.assertEqual(result[0]["status"], "collecting")
        self.bounded.refresh_from_db()
        self.assertLess((self.bounded.next_fetch_at - self.now).total_seconds(), 40)
        with patch("ingestion.data_service.Client", return_value=self.transport(more=False)):
            result = run_data(self.bounded.slug, force=True)
        self.assertEqual(result[0]["status"], "complete")
        self.bounded.refresh_from_db()
        self.assertGreater((self.bounded.next_fetch_at - self.now).total_seconds(), 86000)

    def test_disabled_or_changed_profile_refuses_before_writes(self):
        self.bounded.enabled = False
        with self.assertRaises(FetchError):
            advance_harvest(self.bounded, self.transport())
        self.assertEqual(self.bounded.harvests.count(), 0)
        self.bounded.enabled = True
        self.bounded.config["window_max_bytes"] += 1
        with self.assertRaises(FetchError):
            advance_harvest(self.bounded, self.transport())

    def test_configure_dry_apply_repeat_no_http_and_admin_preservation(self):
        fixtures.StacRecentTests.add_europe(self)
        self.bounded.delete()
        output = io.StringIO()
        call_command("configure_stac_bounded", stdout=output)
        self.assertEqual(DataChannel.objects.filter(config__mode="stac_bounded").count(), 0)
        call_command("configure_stac_bounded", apply=True, stdout=output)
        children = DataChannel.objects.filter(config__mode="stac_bounded")
        self.assertEqual(children.count(), 2)
        self.assertFalse(children.filter(enabled=True).exists())
        self.assertFalse(children.filter(access_approved=True).exists())
        before = list(children.values())
        call_command("configure_stac_bounded", apply=True, stdout=output)
        self.assertEqual(before, list(children.values()))
        children.update(interval_minutes=60)
        with self.assertRaises(CommandError):
            call_command("configure_stac_bounded", apply=True, stdout=output)
