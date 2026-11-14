"""K4 / 4.5.9 isolated trial: quota stop, expiry, then resume on the same cursor.

Limits are overridden for this test database only; nothing touches real data.
"""

import hashlib
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion import test_data_paging as paging
from ingestion.data_paging import advance_harvest, initial_url
from ingestion.data_retention import prune_cache
from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.transport import FetchError


class QuotaExpiryResumeTrial(TestCase):
    setUp = paging.DataPagingTests.setUp
    page = paging.DataPagingTests.page
    transport = paging.DataPagingTests.transport

    def store(self, channel, size, fetched_at, expires_at):
        payload = b"x" * size
        return DataSnapshot.objects.create(
            channel=channel,
            payload=payload,
            size_bytes=size,
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type="application/octet-stream",
            fetched_at=fetched_at,
            expires_at=expires_at,
        )

    def test_quota_stop_expiry_and_resume_keep_cursor_and_valid_copies(self):
        now = timezone.now()
        page_bytes = len(self.page(initial_url(self.channel), 0).body)
        other = DataChannel.objects.create(
            registry=self.channel.registry,
            slug="kept",
            name="Kept",
            kind="geojson",
            region="PL",
            url="https://example.org/",
        )
        kept = self.store(other, 100, now, now + timedelta(days=10))
        expired = self.store(
            other, 10 * page_bytes, now - timedelta(days=31), now - timedelta(days=1)
        )
        quota = kept.size_bytes + 4 * page_bytes
        with override_settings(DATA_STORAGE_MAX_BYTES=quota):
            with self.assertRaisesRegex(FetchError, "quota"):
                advance_harvest(self.channel, self.transport())
            harvest = DataHarvest.objects.get()
            self.assertEqual((harvest.pages, harvest.next_url), (0, harvest.start_url))

            dry = prune_cache(apply=False)
            self.assertEqual(dry["deleted"], 1)
            self.assertTrue(DataSnapshot.objects.filter(pk=expired.pk).exists())
            stats = prune_cache(apply=True)
            self.assertEqual((stats["deleted"], stats["bytes_freed"]), (1, expired.size_bytes))

            first = advance_harvest(DataChannel.objects.get(pk=self.channel.pk), self.transport())
            second = advance_harvest(DataChannel.objects.get(pk=self.channel.pk), self.transport())
        self.assertEqual(first["pages"], 2)
        self.assertEqual((second["status"], second["pages"]), ("complete", 3))
        resumed = DataHarvest.objects.get()
        self.assertEqual((resumed.pk, resumed.start_url), (harvest.pk, harvest.start_url))
        kept.refresh_from_db()
        self.assertEqual((kept.size_bytes, kept.expires_at), (100, now + timedelta(days=10)))
        self.assertFalse(DataSnapshot.objects.filter(pk=expired.pk).exists())
        used = sum(DataSnapshot.objects.values_list("size_bytes", flat=True))
        self.assertLessEqual(used, quota)
