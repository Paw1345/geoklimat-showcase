import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ingestion import test_stac_recent as fixtures
from ingestion.data_paging import digest, initial_url
from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.stac_bounded import RESERVE_BYTES, WINDOW_BYTES, defaults, start_url
from ingestion.stac_refresh import plan, report


class StacRefreshTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacRecentTests.setUp(self)
        self.harvest.config_hash = digest(
            [self.channel.url, self.channel.kind, self.channel.region, self.channel.config]
        )
        self.harvest.status = "retention_review"
        self.harvest.error = "PRIVATE_ERROR_DO_NOT_EXPORT"
        self.harvest.save()
        self.bounded = DataChannel.objects.create(
            slug=self.channel.slug + "-bounded",
            enabled=True,
            access_approved=True,
            next_fetch_at=self.now + timedelta(hours=4),
            **defaults(self.channel),
        )
        with patch("ingestion.data_paging.timezone.now", return_value=self.now):
            for channel, url in (
                (self.head, initial_url(self.head)),
                (self.bounded, start_url(self.bounded)),
            ):
                DataHarvest.objects.create(
                    channel=channel,
                    config_hash=digest([channel.url, channel.kind, channel.region, channel.config]),
                    start_url=url,
                    next_url="",
                    started_at=self.now,
                    status="complete",
                    pages=1,
                    records=1,
                    size_bytes=100,
                )

    def test_reuses_existing_channels_without_resuming_review_or_claiming_full_scope(self):
        row = plan(self.now)["rows"][0]
        self.assertEqual(row["history"]["state"], "retention_review")
        self.assertEqual(row["recommended_action"], "reuse_scheduled_bounded")
        self.assertFalse(row["new_channel_needed"])
        self.assertFalse(row["history_reset_allowed"])
        self.assertFalse(row["bounded"]["due"])
        self.assertEqual(row["bounded"]["next_fetch_at"], self.bounded.next_fetch_at)
        self.assertEqual(row["candidate_search_end"], self.now)
        self.assertFalse(row["candidate_window_pinned"])
        self.assertFalse(row["full_current_30_day_coverage_verified"])
        self.assertIsNone(row["missing_scene_count"])

    def test_combined_budget_counts_active_samples_and_bounded_once(self):
        budget = plan(self.now)["budget"]
        self.assertEqual(budget["future_samples_estimate_bytes"], 100 * 24 * 30)
        self.assertEqual(budget["future_bounded_ceiling_bytes"], WINDOW_BYTES * 30)
        self.assertEqual(
            budget["projected_existing_collection_bytes"], 100 * 24 * 30 + WINDOW_BYTES * 30
        )
        self.assertFalse(budget["additional_collection_approved"])
        self.assertEqual(budget["future_expiry_credited_bytes"], 0)

    @override_settings(DATA_STORAGE_MAX_BYTES=RESERVE_BYTES + WINDOW_BYTES * 30)
    def test_current_free_space_is_not_safe_when_sample_growth_is_added(self):
        self.assertFalse(plan(self.now)["budget"]["fits_existing_collection_scenario"])

    def test_invalid_or_revoked_bounded_profile_is_not_reusable(self):
        self.bounded.config = {**self.bounded.config, "window_max_pages": 999}
        self.bounded.save()
        result = plan(self.now)
        self.assertEqual(result["rows"][0]["recommended_action"], "review_existing_profile")
        self.assertIsNone(result["budget"]["future_bounded_ceiling_bytes"])
        self.assertIsNone(result["budget"]["fits_existing_collection_scenario"])
        self.channel.access_approved = False
        self.channel.save()
        self.assertFalse(plan(self.now)["rows"][0]["recent"]["profile_valid"])

    def test_missing_profiles_do_not_create_channels(self):
        self.bounded.slug = "renamed-bounded"
        self.bounded.save()
        before = DataChannel.objects.count()
        row = plan(self.now)["rows"][0]
        self.assertEqual(row["bounded"]["state"], "missing")
        self.assertEqual(row["recommended_action"], "review_existing_profile")
        self.assertIsNone(row["new_channel_needed"])
        self.assertEqual(DataChannel.objects.count(), before)

    def test_stale_sample_and_unknown_active_bounded_cost_stay_unknown(self):
        self.head.harvests.update(started_at=self.now - timedelta(days=1))
        self.assertIsNone(plan(self.now)["budget"]["projected_existing_collection_bytes"])
        self.head.enabled = False
        self.head.save()
        DataChannel.objects.create(
            registry=self.channel.registry,
            name="Foreign",
            slug="foreign",
            kind="stac",
            enabled=True,
            access_approved=True,
            config={"mode": "stac_bounded"},
        )
        self.assertIsNone(plan(self.now)["budget"]["future_bounded_ceiling_bytes"])

    def test_disabled_channel_has_zero_future_cost_and_no_auto_activation(self):
        self.bounded.enabled = False
        self.bounded.save()
        result = plan(self.now)
        self.assertEqual(result["budget"]["future_bounded_ceiling_bytes"], 0)
        self.assertEqual(result["rows"][0]["recommended_action"], "review_existing_profile")
        self.bounded.refresh_from_db()
        self.assertFalse(self.bounded.enabled)

    def test_cli_no_http_payloads_or_database_mutation(self):
        before = (
            list(DataChannel.objects.values()),
            list(DataHarvest.objects.values()),
            DataSnapshot.objects.count(),
        )
        output = io.StringIO()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            with CaptureQueriesContext(connection) as queries:
                call_command("stac_refresh_plan", stdout=output)
        self.assertTrue(json.loads(output.getvalue())["read_only"])
        self.assertNotIn("PRIVATE_ERROR", output.getvalue())
        self.assertNotIn("next_url", output.getvalue())
        self.assertNotIn("https://", output.getvalue())
        self.assertFalse(any('"payload"' in query["sql"] for query in queries))
        self.assertEqual(
            before,
            (
                list(DataChannel.objects.values()),
                list(DataHarvest.objects.values()),
                DataSnapshot.objects.count(),
            ),
        )

    def test_read_only_transaction_rejects_writes(self):
        with patch(
            "ingestion.stac_refresh.plan",
            side_effect=lambda *args: DataChannel.objects.filter(pk=self.bounded.pk).update(
                enabled=False
            ),
        ):
            with self.assertRaises(DatabaseError):
                report()
        self.assertTrue(DataChannel.objects.filter(pk=self.bounded.pk).exists())

    def test_future_or_changed_scope_cannot_claim_reusable_evidence(self):
        self.bounded.harvests.update(config_hash="f" * 64)
        result = plan(self.now)
        self.assertFalse(result["rows"][0]["bounded"]["profile_valid"])
        self.assertIsNone(result["budget"]["fits_existing_collection_scenario"])
        with self.assertRaises(ValueError):
            plan(self.now.replace(tzinfo=None))

    def test_staff_panel_and_json_share_plan_get_only(self):
        self.assertEqual(self.client.get("/data/stac/").status_code, 302)
        user = get_user_model().objects.create_user(username="refresh", is_staff=False)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/data/stac/").status_code, 302)
        user.is_staff = True
        user.save()
        self.assertContains(self.client.get("/data/stac/"), "Plan świeżego okna")
        response = self.client.get("/data/stac/?format=json")
        self.assertFalse(response.json()["refresh_plan"]["execution_performed"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post("/data/stac/").status_code, 405)
