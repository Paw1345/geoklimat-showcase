import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.data_paging import RETENTION_REVIEW, advance_harvest, digest, initial_url
from ingestion.data_service import query_url, run_data
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response


class StacRetentionReviewTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="review", sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=52, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug="stac-review",
            name="STAC",
            kind="stac",
            region="EU",
            enabled=True,
            access_approved=True,
            interval_minutes=60,
            url="https://stac.dataspace.copernicus.eu/v1/search",
            config={
                "pagination": "stac_get",
                "collection": "sentinel-2-l2a",
                "bbox": [-31.3, 27.5, 35.7, 70.1],
            },
        )
        start = initial_url(self.channel)
        self.harvest = DataHarvest.objects.create(
            channel=self.channel,
            config_hash=digest(
                [self.channel.url, self.channel.kind, self.channel.region, self.channel.config]
            ),
            start_url=start,
            next_url=query_url(start, token="next2"),
            pages=2,
            records=20,
            size_bytes=100,
            visited_urls=["prior_url"],
            page_fingerprints=["prior_page"],
        )
        self.old_copy = DataSnapshot.objects.create(
            channel=self.channel,
            fetched_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
            payload=b"{}",
            size_bytes=2,
            sha256="fixture",
            media_type="application/json",
            summary={"kind": "stac"},
        )

    def feature(self, identity, days):
        return {
            "type": "Feature",
            "id": identity,
            "properties": {"datetime": (timezone.now() - timedelta(days=days)).isoformat()},
        }

    def transport(self, features, more=True):
        client = Mock()
        client.get.side_effect = lambda url: Response(
            200,
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": features,
                    "links": [{"rel": "next", "href": query_url(url, token="next3")}]
                    if more
                    else [],
                }
            ).encode(),
            {},
            url,
        )
        return client

    def cursor_state(self):
        self.harvest.refresh_from_db()
        return (
            self.harvest.start_url,
            self.harvest.next_url,
            self.harvest.pages,
            self.harvest.records,
            self.harvest.size_bytes,
            self.harvest.visited_urls,
            self.harvest.page_fingerprints,
            self.harvest.finished_at,
        )

    def test_old_page_pauses_without_copy_or_cursor_move_even_at_provider_end(self):
        baseline = self.cursor_state()
        for more in (True, False):
            with self.subTest(more=more):
                DataHarvest.objects.filter(pk=self.harvest.pk).update(status="running")
                result = advance_harvest(
                    self.channel, self.transport([self.feature("old", 31)], more)
                )
                self.assertEqual(result["status"], RETENTION_REVIEW)
                self.assertFalse(result["scan_complete"])
                self.assertEqual(self.cursor_state(), baseline)
                self.assertEqual(DataSnapshot.objects.count(), 1)
                self.old_copy.refresh_from_db()
                self.assertEqual(bytes(self.old_copy.payload), b"{}")

    def test_repeat_and_force_no_http_no_fresh_scan_status_visible(self):
        client = self.transport([self.feature("old", 31)])
        with patch("ingestion.data_service.Client", return_value=client):
            result = run_data(self.channel.slug, force=True)[0]
            self.assertEqual(result["status"], RETENTION_REVIEW)
            self.channel.refresh_from_db()
            self.assertEqual(self.channel.last_status, RETENTION_REVIEW)
            self.assertTrue(self.channel.last_error)
            self.assertGreater(self.channel.next_fetch_at, timezone.now() + timedelta(minutes=59))
            client.get.reset_mock()
            self.assertEqual(run_data(self.channel.slug, force=True)[0]["status"], RETENTION_REVIEW)
            client.get.assert_not_called()
            self.assertEqual(
                run_data(self.channel.slug, on_demand=True)[0]["status"], RETENTION_REVIEW
            )
            client.get.assert_not_called()
        self.assertEqual(DataHarvest.objects.count(), 1)
        self.assertContains(self.client.get(f"/data/{self.channel.pk}/"), "wymagany przegląd okna")

    def test_mixed_page_stores_only_retained_records_and_can_complete(self):
        result = advance_harvest(
            self.channel,
            self.transport([self.feature("old", 31), self.feature("recent", 1)], more=False),
        )
        self.assertEqual(result["status"], "complete")
        snapshot = DataSnapshot.objects.order_by("-pk").first()
        self.assertEqual(snapshot.summary["raw_records"], 2)
        self.assertEqual(snapshot.summary["records"], 1)
        self.assertEqual(json.loads(bytes(snapshot.payload))["features"][0]["id"], "recent")

    def test_missing_timestamps_requires_review_not_expiry_or_complete_claim(self):
        result = advance_harvest(
            self.channel,
            self.transport([{"type": "Feature", "id": "unknown", "properties": {}}], more=False),
        )
        self.assertEqual(result["status"], RETENTION_REVIEW)
        self.assertIn("timestamps", result["error"])
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_first_page_audit_cannot_clear_or_enable_paused_scan(self):
        advance_harvest(self.channel, self.transport([self.feature("old", 31)]))
        with patch("ingestion.transport.Client.get") as get:
            for enable in (True, False):
                with self.assertRaises(CommandError):
                    call_command(
                        "audit_data", source=self.channel.slug, enable=enable, stdout=io.StringIO()
                    )
            get.assert_not_called()
        self.harvest.refresh_from_db()
        self.assertEqual(self.harvest.status, RETENTION_REVIEW)

    def test_provider_empty_with_continuation_still_fails_without_review(self):
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.transport([]))
        self.harvest.refresh_from_db()
        self.assertEqual(self.harvest.status, "failed")
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_true_empty_provider_end_keeps_existing_completion_semantics(self):
        self.assertEqual(
            advance_harvest(self.channel, self.transport([], False))["status"], "complete"
        )

    def test_revoked_approval_during_get_is_not_restored_or_marked_review(self):
        client = self.transport([self.feature("old", 31)])
        original = client.get.side_effect

        def revoke(url):
            DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
            return original(url)

        client.get.side_effect = revoke
        with patch("ingestion.data_service.Client", return_value=client):
            self.assertEqual(run_data(self.channel.slug, force=True)[0]["status"], "failed")
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.access_approved)
        self.assertEqual(DataSnapshot.objects.count(), 1)
