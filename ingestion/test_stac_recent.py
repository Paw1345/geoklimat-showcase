import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from core import test_stac_coverage as fixtures
from ingestion.data_paging import advance_harvest, initial_url
from ingestion.data_service import collect, query_url, run_data
from ingestion.management.commands.configure_stac_recent import defaults
from ingestion.models import DataChannel, DataSnapshot
from ingestion.stac_coverage import coverage, search_scope
from ingestion.stac_integrity import verify_batch
from ingestion.stac_recent import configuration, probe
from ingestion.transport import FetchError, Response


class StacRecentTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacCoverageTests.setUp(self)
        self.channel.slug = "copernicus-scenes-pl"
        self.channel.access_approved = True
        self.channel.enabled = True
        self.channel.save(update_fields=["slug", "access_approved", "enabled"])
        self.head = DataChannel.objects.create(
            slug=self.channel.slug + "-recent",
            access_approved=True,
            enabled=True,
            next_fetch_at=self.now - timedelta(hours=1),
            **defaults(self.channel),
        )

    def response(self, url, more=True, change=None):
        page = int(dict(parse_qsl(urlsplit(url).query)).get("token", "1"))
        data = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": f"scene-{page}",
                    "collection": "sentinel-2-l2a",
                    "properties": {"datetime": (timezone.now() - timedelta(hours=1)).isoformat()},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                    },
                }
            ],
            "links": [{"rel": "next", "href": query_url(url, token=page + 1)}] if more else [],
        }
        if change:
            change(data)
        return Response(200, json.dumps(data).encode(), {}, url)

    def transport(self, more=True, change=None):
        client = Mock()
        client.get.side_effect = lambda url: self.response(url, more=more, change=change)
        return client

    def add_europe(self):
        DataChannel.objects.create(
            registry=self.channel.registry,
            slug="copernicus-scenes-europe",
            name="Europe",
            kind="stac",
            region="EU",
            url=self.channel.url,
            enabled=True,
            access_approved=True,
            config={
                "bbox": [-31.3, 27.5, 35.7, 70.1],
                "collection": "sentinel-2-l2a",
                "pagination": "stac_get",
            },
        )

    def test_two_page_bounded_cursor_and_history_unchanged(self):
        before = self.channel.harvests.values().get()
        result = advance_harvest(self.head, self.transport())
        head = self.head.harvests.first()
        self.assertEqual(result["status"], "sampled")
        self.assertEqual(head.status, "bounded")
        self.assertEqual(head.pages, 2)
        self.assertTrue(head.next_url)
        self.assertIsNotNone(head.finished_at)
        self.assertEqual(self.channel.harvests.values().get(), before)
        scope = search_scope(self.head, head)
        self.assertEqual(scope["search_window_hours"], 24)
        self.assertEqual(scope["acquisition_mode"], "recent_sample")
        summary = coverage(self.head, head, timezone.now())
        self.assertFalse(summary["scan_complete"])
        self.assertEqual(summary["issues"], [])
        self.assertTrue(verify_batch(self.head, head, timezone.now())["batch_verified"])

    def test_next_cycle_starts_new_window_does_not_resume_previous_head(self):
        with patch("django.utils.timezone.now", return_value=self.now):
            advance_harvest(self.head, self.transport())
        old = self.head.harvests.values().get()
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(hours=1)):
            advance_harvest(self.head, self.transport())
        self.assertEqual(self.head.harvests.count(), 2)
        self.assertEqual(self.head.harvests.get(pk=old["id"]).next_url, old["next_url"])
        self.assertNotEqual(self.head.harvests.first().start_url, old["start_url"])

    def test_failed_page_keeps_committed_copy_and_next_attempt_is_fresh(self):
        client = self.transport()
        client.get.side_effect = [self.response(initial_url(self.head)), FetchError("fixture")]
        with self.assertRaises(FetchError):
            advance_harvest(self.head, client)
        old = self.head.harvests.first()
        self.assertEqual((old.status, old.pages), ("failed", 1))
        self.assertEqual(self.head.snapshots.count(), 1)
        advance_harvest(self.head, self.transport())
        self.assertEqual(self.head.harvests.count(), 2)
        old.refresh_from_db()
        self.assertEqual(old.status, "failed")

    def test_no_next_page_complete_only_for_short_window(self):
        result = advance_harvest(self.head, self.transport(more=False))
        self.assertEqual(result["status"], "sampled")
        self.assertEqual(self.head.harvests.first().status, "complete")
        summary = coverage(self.head, self.head.harvests.first(), timezone.now())
        self.assertFalse(summary["full_current_30_day_coverage_verified"])
        self.assertEqual(summary["search_window_hours"], 24)

    def test_profile_and_stale_custom_url_rejected_before_http(self):
        client = self.transport()
        self.head.config["head_max_pages"] = 3
        with self.assertRaises(FetchError):
            collect(self.head, client)
        client.get.assert_not_called()
        self.head.config = configuration(self.channel)
        with patch("django.utils.timezone.now", return_value=self.now - timedelta(hours=1)):
            old_url = initial_url(self.head)
        with self.assertRaises(FetchError):
            collect(self.head, client, request_url=old_url)
        with self.assertRaises(FetchError):
            collect(self.head, client, request_url="https://example.org/")
        client.get.assert_not_called()

    def test_outside_window_collection_geometry_and_page_size_refused(self):
        changes = [
            lambda d: d["features"][0]["properties"].update(
                datetime=(timezone.now() - timedelta(days=2)).isoformat()
            ),
            lambda d: d["features"][0].update(collection="other"),
            lambda d: d["features"][0].update(geometry=None),
            lambda d: d.update(features=[{**d["features"][0], "id": str(i)} for i in range(11)]),
        ]
        for change in changes:
            with self.assertRaises(FetchError):
                collect(self.head, self.transport(change=change))
        self.assertEqual(self.head.snapshots.count(), 0)

    def test_parent_policy_revoked_blocks_sample(self):
        self.channel.access_approved = False
        self.channel.save(update_fields=["access_approved"])
        with self.assertRaises(FetchError):
            collect(self.head, self.transport())

    def test_probe_read_only_and_repeated_page_rejected(self):
        result = probe(self.head, self.transport())
        self.assertEqual((result["pages"], result["records"]), (2, 2))
        self.assertTrue(result["more_available"])
        self.assertFalse(result["full_day_coverage_verified"])
        self.assertEqual(self.head.harvests.count(), 0)
        self.assertEqual(self.head.snapshots.count(), 0)
        with self.assertRaisesRegex(FetchError, "repeated page"):
            probe(self.head, self.transport(change=lambda d: d["features"][0].update(id="same")))

    def test_run_data_hourly_not_history_fast_poll(self):
        with patch("ingestion.data_service.Client", return_value=self.transport()):
            result = run_data(self.head.slug, force=True)
        self.assertEqual(result[0]["status"], "sampled")
        self.head.refresh_from_db()
        self.assertGreater(self.head.next_fetch_at, timezone.now() + timedelta(minutes=59))

    @override_settings(DATA_STORAGE_MAX_BYTES=1)
    def test_quota_failure_does_not_advance_or_touch_history(self):
        before = self.channel.harvests.values().get()
        with self.assertRaises(FetchError):
            advance_harvest(self.head, self.transport())
        self.assertEqual(self.head.harvests.first().pages, 0)
        self.assertEqual(self.channel.harvests.values().get(), before)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_configure_dry_apply_disabled_repeat_and_admin_guard(self):
        self.head.delete()
        self.add_europe()
        count = DataChannel.objects.count()
        call_command("configure_stac_recent", stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), count)
        call_command("configure_stac_recent", apply=True, stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), count + 2)
        self.assertFalse(DataChannel.objects.get(slug=self.channel.slug + "-recent").enabled)
        call_command("configure_stac_recent", apply=True, stdout=io.StringIO())
        head = DataChannel.objects.get(slug=self.channel.slug + "-recent")
        head.interval_minutes = 120
        head.save(update_fields=["interval_minutes"])
        with self.assertRaises(CommandError):
            call_command("configure_stac_recent", apply=True, stdout=io.StringIO())
        head.refresh_from_db()
        self.assertEqual(head.interval_minutes, 120)

    def test_activation_requires_fresh_probe_and_preserves_parent(self):
        self.head.delete()
        self.add_europe()
        before = self.channel.harvests.values().get()
        with patch(
            "ingestion.management.commands.configure_stac_recent.probe",
            side_effect=FetchError("no"),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "configure_stac_recent", activate=True, apply=True, stdout=io.StringIO()
                )
        self.assertEqual(DataChannel.objects.filter(config__mode="stac_recent").count(), 0)
        with patch(
            "ingestion.management.commands.configure_stac_recent.Client",
            side_effect=lambda: self.transport(),
        ):
            call_command("configure_stac_recent", activate=True, stdout=io.StringIO())
            self.assertEqual(DataChannel.objects.filter(config__mode="stac_recent").count(), 0)
            call_command("configure_stac_recent", activate=True, apply=True, stdout=io.StringIO())
        self.assertEqual(
            DataChannel.objects.filter(
                config__mode="stac_recent", enabled=True, access_approved=True
            ).count(),
            2,
        )
        self.assertEqual(self.channel.harvests.values().get(), before)

    def test_bounded_wait_retries_only_lock_and_invalid_wait_refused(self):
        self.add_europe()
        with patch("ingestion.management.commands.configure_stac_recent.batch_lock") as lock:
            lock.return_value.__enter__.side_effect = [False, True]
            with patch("ingestion.management.commands.configure_stac_recent.time.sleep"):
                call_command("configure_stac_recent", wait_seconds=1, stdout=io.StringIO())
            self.assertEqual(lock.call_count, 2)
        for wait in [-1, 31]:
            with self.assertRaises(CommandError):
                call_command("configure_stac_recent", wait_seconds=wait, stdout=io.StringIO())

    def test_public_cards_explain_sample_and_staff_stac_view_exports(self):
        from django.contrib.auth import get_user_model

        advance_harvest(self.head, self.transport())
        self.assertContains(self.client.get(f"/data/{self.head.pk}/"), "Świeża próbka STAC")
        self.assertContains(self.client.get("/data/"), "Nie jest pełnym katalogiem dobowym")
        self.client.force_login(
            get_user_model().objects.create_user(username="staff", is_staff=True)
        )
        self.assertContains(self.client.get("/data/stac/"), "maks. 2 strony / 20 scen")
        harvest = self.head.harvests.first()
        query = f"/data/stac/scenes/?channel={self.head.pk}&harvest={harvest.pk}&format=geojson"
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json.loads(response.content)["features"]), 2)
