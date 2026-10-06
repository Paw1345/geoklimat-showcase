import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings

from core import test_stac_coverage as fixtures
from ingestion.data_paging import digest, initial_url
from ingestion.models import DataChannel, DataHarvest, DataSnapshot
from ingestion.stac_capacity import MIB, capacity, report
from ingestion.stac_recent import configuration


@override_settings(DATA_STORAGE_MAX_BYTES=1000 * MIB, DATA_AUTO_MAX_BYTES=5 * MIB)
class StacCapacityTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacCoverageTests.setUp(self)
        self.channel.slug = "copernicus-scenes-pl"
        self.channel.enabled = self.channel.access_approved = True
        self.channel.save()
        self.head = DataChannel.objects.create(
            registry=self.channel.registry,
            slug=self.channel.slug + "-recent",
            name="Recent",
            kind="stac",
            region="PL",
            url=self.channel.url,
            config=configuration(self.channel),
            enabled=True,
            access_approved=True,
            interval_minutes=60,
        )
        self.sample = DataHarvest.objects.create(
            channel=self.head,
            config_hash=digest([self.head.url, self.head.kind, self.head.region, self.head.config]),
            started_at=self.now,
            start_url=initial_url(self.head),
            next_url="",
            status="complete",
            pages=1,
            records=0,
            size_bytes=425,
        )

    def test_additive_budget_counts_expired_copies_and_never_approves_activation(self):
        fixtures.StacCoverageTests.copy(self)
        DataSnapshot.objects.update(size_bytes=100 * MIB, expires_at=self.now - timedelta(days=1))
        result = capacity(self.now, 10, 100)
        self.assertEqual(result["used_declared_bytes"], 100 * MIB)
        self.assertEqual(result["additional_retention_budget_bytes"], 300 * MIB)
        self.assertEqual(result["max_additional_daily_bytes_before_other_growth"], 800 * MIB // 30)
        self.assertTrue(result["fits_current_headroom"])
        self.assertFalse(result["activation_approved"])
        self.assertFalse(result["provider_daily_volume_known"])
        self.assertFalse(result["payload_integrity_verified"])

    def test_empty_sample_has_copy_cost_not_full_day_volume(self):
        result = capacity(self.now, 1, 0)
        row = result["rows"][0]
        self.assertEqual(row["sample_state"], "empty_sample")
        self.assertEqual(row["future_samples_retention_estimate_bytes"], 425 * 24 * 30)
        self.assertEqual(row["future_samples_retention_ceiling_bytes"], 2 * 5 * MIB * 24 * 30)
        self.assertIsNone(row["provider_daily_volume_bytes"])

    def test_future_sample_cost_can_invalidate_current_headroom_fit(self):
        self.sample.size_bytes = 5 * MIB
        self.sample.records = 10
        self.sample.save()
        result = capacity(self.now, 1, 0)
        self.assertTrue(result["fits_current_headroom"])
        self.assertFalse(result["fits_with_sample_estimate"])

    def test_stale_failed_or_changed_profiles_do_not_project_fake_zero(self):
        for changes in (
            {"started_at": self.now - timedelta(hours=3)},
            {"status": "failed"},
            {"config_hash": "b" * 64},
            {"started_at": self.now + timedelta(seconds=1)},
        ):
            DataHarvest.objects.filter(pk=self.sample.pk).update(**changes)
            result = capacity(self.now, 1, 0)
            self.assertIsNone(result["future_samples_retention_estimate_bytes"])
            self.assertIsNone(result["fits_with_sample_estimate"])

    def test_disabled_head_does_not_consume_future_projection(self):
        self.head.enabled = False
        self.head.save()
        result = capacity(self.now, 1, 0)
        self.assertEqual(result["future_samples_retention_estimate_bytes"], 0)
        self.assertEqual(result["rows"][0]["future_samples_retention_ceiling_bytes"], 0)

    def test_over_quota_and_reserve_shortfall_are_not_negative_headroom(self):
        fixtures.StacCoverageTests.copy(self)
        DataSnapshot.objects.update(size_bytes=1100 * MIB)
        result = capacity(self.now, 1, 100)
        self.assertEqual(result["over_quota_bytes"], 100 * MIB)
        self.assertEqual(result["reserve_shortfall_bytes"], 100 * MIB)
        self.assertEqual(result["max_additional_daily_bytes_before_other_growth"], 0)
        self.assertFalse(result["fits_current_headroom"])

    def test_cli_validates_bounds_and_is_read_only(self):
        before = (DataHarvest.objects.count(), DataSnapshot.objects.count())
        output = io.StringIO()
        call_command("stac_capacity", daily_budget_mib=10, reserve_mib=100, stdout=output)
        self.assertTrue(json.loads(output.getvalue())["read_only"])
        self.assertEqual(before, (DataHarvest.objects.count(), DataSnapshot.objects.count()))
        for value in (0, -1, 1025):
            with self.assertRaises(CommandError):
                call_command("stac_capacity", daily_budget_mib=value, stdout=io.StringIO())
        with self.assertRaises(ValueError):
            capacity(self.now.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            capacity(self.now, True, 0)
        with self.assertRaises(ValueError):
            capacity(self.now, 1, 16385)

    def test_report_transaction_rejects_writes(self):
        with patch("ingestion.stac_capacity.capacity", side_effect=lambda *a: self.head.delete()):
            with self.assertRaises(DatabaseError):
                report()
        self.assertTrue(DataChannel.objects.filter(pk=self.head.pk).exists())

    def test_panel_and_json_show_scenario_only_to_staff(self):
        self.assertEqual(self.client.get("/data/stac/").status_code, 302)
        self.client.force_login(
            get_user_model().objects.create_user(username="budget", is_staff=True)
        )
        self.assertContains(self.client.get("/data/stac/"), "Budżet rozszerzenia STAC")
        response = self.client.get("/data/stac/?format=json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["capacity"]["activation_approved"])
        self.assertIn("no-store", response["Cache-Control"])
