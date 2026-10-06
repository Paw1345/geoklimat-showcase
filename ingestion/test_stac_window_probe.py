import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase

from ingestion import test_stac_recent as fixtures
from ingestion.data_service import query_url
from ingestion.models import DataHarvest, DataSnapshot
from ingestion.stac_window_probe import measure, plan
from ingestion.transport import FetchError


class StacWindowProbeTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacRecentTests.setUp(self)

    def response(self, *args, **kwargs):
        return fixtures.StacRecentTests.response(self, *args, **kwargs)

    def transport(self, *args, **kwargs):
        return fixtures.StacRecentTests.transport(self, *args, **kwargs)

    def test_default_command_is_no_http_dry_plan_and_no_writes(self):
        with patch("ingestion.management.commands.stac_window_probe.Client") as client:
            output = io.StringIO()
            call_command("stac_window_probe", region="PL", stdout=output)
            client.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertFalse(result["network_requested"])
        self.assertEqual(result["max_pages"], 6)
        self.assertEqual(DataHarvest.objects.count(), 1)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_reads_more_than_head_without_changing_its_profile_or_history(self):
        before = self.channel.harvests.values().get()
        result = measure(self.head, self.transport(), pages=6)
        self.assertEqual(result["pages_validated"], 6)
        self.assertEqual(result["unique_ids_in_validated_pages"], 6)
        self.assertEqual(result["stop_reason"], "page_budget")
        self.assertTrue(result["remaining_pagination_known"])
        self.assertIsNone(result["window_bytes_if_pagination_end"])
        self.assertFalse(result["stored"])
        self.assertFalse(result["activation_approved"])
        self.assertEqual(self.channel.harvests.values().get(), before)
        self.assertEqual(DataSnapshot.objects.count(), 0)
        self.head.refresh_from_db()
        self.assertEqual(self.head.config["head_max_pages"], 2)

    def test_empty_end_is_not_full_day_claim(self):
        result = measure(
            self.head, self.transport(more=False, change=lambda d: d.update(features=[]))
        )
        self.assertTrue(result["pagination_end_observed"])
        self.assertEqual(result["records_validated"], 0)
        self.assertGreater(result["window_bytes_if_pagination_end"], 0)
        self.assertFalse(result["full_day_coverage_verified"])

    def test_normalized_byte_budget_stops_before_next_get(self):
        payload = json.dumps({"features": [{"id": "only"}]}).encode().ljust(1024**2, b" ")
        summary = {
            "request_url": "",
            "next_url": "",
            "raw_records": 1,
            "page_fingerprint": "one",
            "records": 1,
        }

        def fake_collect(channel, client, request_url):
            summary.update(request_url=request_url, next_url=query_url(request_url, token=2))
            return payload, "application/json", summary

        with patch("ingestion.stac_window_probe.collect", side_effect=fake_collect):
            result = measure(self.head, self.transport(), budget_mib=1)
        self.assertFalse(result["failed"])
        self.assertEqual(result["pages_validated"], 1)
        self.assertEqual(result["stop_reason"], "byte_budget")

    def test_raw_response_budget_and_invalid_pages_never_store(self):
        for change in (
            lambda d: d.update(extra="x" * (2 * 1024**2)),
            lambda d: d["features"][0]["properties"].update(datetime="2000-01-01T00:00:00Z"),
            lambda d: d["links"][0].update(href="https://example.com/private"),
        ):
            result = measure(self.head, self.transport(change=change), budget_mib=1)
            self.assertTrue(result["failed"])
            self.assertEqual(result["pages_validated"], 0)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_duplicate_scene_across_pages_fails_and_keeps_only_validated_counts(self):
        result = measure(
            self.head, self.transport(change=lambda d: d["features"][0].update(id="same"))
        )
        self.assertTrue(result["failed"])
        self.assertEqual(result["pages_validated"], 1)
        self.assertFalse(result["pagination_end_observed"])

    def test_time_gate_does_not_launch_next_request(self):
        client = self.transport()
        with patch("ingestion.stac_window_probe.time.monotonic", side_effect=[0, 61, 61]):
            result = measure(self.head, client)
        self.assertEqual(result["stop_reason"], "time_budget")
        self.assertFalse(result["remaining_pagination_known"])
        client.get.assert_not_called()

    def test_in_flight_request_may_finish_after_time_gate_but_page_is_not_accepted(self):
        client = self.transport(more=False)
        with patch("ingestion.stac_window_probe.time.monotonic", side_effect=[0, 0, 61, 61]):
            result = measure(self.head, client)
        self.assertEqual(result["stop_reason"], "time_budget")
        self.assertEqual(result["pages_validated"], 0)
        self.assertFalse(result["pagination_end_observed"])
        client.get.assert_called_once()

    def test_failed_request_returns_safe_partial_result_and_cli_nonzero(self):
        client = self.transport()
        client.get.side_effect = FetchError("private raw error must not be exported")
        with patch("ingestion.management.commands.stac_window_probe.Client", return_value=client):
            output = io.StringIO()
            with self.assertRaises(CommandError):
                call_command("stac_window_probe", region="PL", fetch=True, stdout=output)
        self.assertNotIn("private raw", output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["failed"])

    def test_invalid_limits_or_revoked_parent_refuse_before_get(self):
        for limits in (
            {"pages": 0},
            {"pages": 11},
            {"budget_mib": 17},
            {"seconds": 0},
            {"seconds": 91},
            {"pages": True},
        ):
            with self.assertRaises(ValueError):
                plan(self.head, **limits)
        self.channel.access_approved = False
        self.channel.save()
        client = self.transport()
        with self.assertRaises(FetchError):
            measure(self.head, client)
        client.get.assert_not_called()
