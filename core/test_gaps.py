from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.gaps import report
from ingestion.models import DataChannel, IngestionRun, RegistryEntry, RegistryRelease, Source


class GapReportTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def source(self, slug, **fields):
        defaults = {
            "name": slug,
            "url": f"https://www.gov.pl/{slug}",
            "adapter": "feed",
            "enabled": True,
            "interval_minutes": 60,
        }
        return Source.objects.create(slug=slug, **(defaults | fields))

    def rows(self):
        return {row["name"]: row for row in report(self.now)["sources"]}

    def test_source_is_late_only_after_two_intervals(self):
        self.source("fresh", last_success_at=self.now - timedelta(minutes=119))
        self.source("late", last_success_at=self.now - timedelta(minutes=121))
        self.source("never")
        self.source("off", enabled=False)
        rows = self.rows()
        self.assertEqual(set(rows), {"fresh", "late", "never"})
        self.assertFalse(rows["fresh"]["late"])
        self.assertTrue(rows["late"]["late"])
        self.assertTrue(rows["never"]["late"])
        self.assertIsNone(rows["never"]["minutes_since_success"])
        self.assertEqual(report(self.now)["sources_late"], 2)
        self.assertEqual(report(self.now)["sources"][-1]["name"], "fresh")

    def test_longest_gap_counts_successes_only_and_the_open_tail(self):
        source = self.source("gap", last_success_at=self.now - timedelta(hours=1))
        for hours, status in [
            (30, "success"),
            (20, "failed"),
            (10, "not_modified"),
            (1, "partial"),
        ]:
            IngestionRun.objects.create(
                source=source, status=status, started_at=self.now - timedelta(hours=hours)
            )
        # 14 days window: start -> 30 h ago is the longest pause (306 h).
        self.assertEqual(self.rows()["gap"]["longest_gap_minutes"], (14 * 24 - 30) * 60)
        IngestionRun.objects.create(
            source=source, status="success", started_at=self.now - timedelta(days=14)
        )
        IngestionRun.objects.create(
            source=source, status="success", started_at=self.now - timedelta(days=13, hours=23)
        )
        # Now the longest pause is between the old run and the one 30 h ago.
        self.assertEqual(self.rows()["gap"]["longest_gap_minutes"], (13 * 24 + 23 - 30) * 60)

    def test_source_without_runs_has_the_whole_window_as_gap(self):
        self.source("quiet", last_success_at=self.now)
        self.assertEqual(self.rows()["quiet"]["longest_gap_minutes"], 14 * 24 * 60)

    def test_channel_is_late_when_failed_or_not_checked(self):
        release = RegistryRelease.objects.create(filename="test", sha256="f" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=1, release=release, master={}, technical={}, organization="T", name="T"
        )

        def channel(slug, **fields):
            defaults = {
                "registry": entry,
                "name": slug,
                "kind": "geojson",
                "url": f"https://www.gov.pl/{slug}",
                "region": "PL",
                "attribution": "T",
                "enabled": True,
                "interval_minutes": 60,
            }
            return DataChannel.objects.create(slug=slug, **(defaults | fields))

        channel("ok", last_status="stored", checked_at=self.now - timedelta(minutes=30))
        channel("failed", last_status="failed", checked_at=self.now - timedelta(minutes=30))
        channel("old", last_status="unchanged", checked_at=self.now - timedelta(minutes=121))
        channel("never")
        channel("off", enabled=False)
        data = report(self.now)
        rows = {row["name"]: row["late"] for row in data["channels"]}
        self.assertEqual(rows, {"ok": False, "failed": True, "old": True, "never": True})
        self.assertEqual(data["channels_late"], 3)


class GapViewTests(TestCase):
    def test_staff_only_html_and_json(self):
        Source.objects.create(
            slug="s", name="Źródło", url="https://www.gov.pl/s", adapter="feed", enabled=True
        )
        self.assertEqual(self.client.get("/quality/gaps/").status_code, 302)
        user = get_user_model().objects.create_user("staff", password="x", is_staff=True)
        self.client.force_login(user)
        response = self.client.get("/quality/gaps/")
        self.assertContains(response, "Źródło")
        self.assertContains(response, "spóźnione")
        data = self.client.get("/quality/gaps/?format=json").json()
        self.assertEqual(data["sources_late"], 1)
        self.assertEqual(self.client.post("/quality/gaps/").status_code, 405)
