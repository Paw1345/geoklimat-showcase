import hashlib
import io
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.data_views import export_payload
from ingestion.bdl_samples import RESERVE
from ingestion.bdl_scan import SLUG, advance, defaults, fetch_page, page_budget, page_url
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

RAW = b'{ "original": true }\n'
MANIFEST = {
    "scope_sha256": "a" * 64,
    "identity_manifest": [{"id": 1, "source_year": 2026}, {"id": 2, "source_year": 2026}],
}


class BdlScanTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="bdlscan", sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=39,
            release=release,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        self.channel = DataChannel.objects.create(
            slug=SLUG, name="RDLP scan", enabled=False, access_approved=True, **defaults(registry)
        )
        self.manifest = patch("ingestion.bdl_scan.fetch_manifest", return_value=MANIFEST).start()
        self.page = patch(
            "ingestion.bdl_scan.fetch_page",
            side_effect=lambda client, expected, **kwargs: (RAW, page_url(expected["id"])),
        ).start()
        self.addCleanup(patch.stopall)

    def test_resume_atomic_original_export_complete_no_repeat_http(self):
        first = advance(self.channel, Mock(), False)
        snapshot = DataSnapshot.objects.get(pk=first["snapshot_id"])
        self.assertEqual(bytes(snapshot.payload), RAW)
        self.assertEqual(snapshot.sha256, hashlib.sha256(RAW).hexdigest())
        self.assertEqual(export_payload(snapshot), RAW)
        self.assertEqual(snapshot.summary["feature_ids"], [1])
        harvest = self.channel.harvests.get()
        self.assertEqual((harvest.records, harvest.pages, harvest.next_url), (1, 1, page_url(2)))
        second = advance(self.channel, Mock(), False)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["records"], 2)
        before = self.manifest.call_count
        self.assertEqual(advance(self.channel, Mock(), False)["status"], "complete")
        self.assertEqual(self.manifest.call_count, before)
        self.assertEqual(DataSnapshot.objects.count(), 2)
        snapshot.summary["url"] = "https://evil.invalid/"
        self.assertIsNone(export_payload(snapshot))

    def test_dry_run_does_not_write_cursor_or_copy(self):
        self.assertEqual(advance(self.channel, Mock())["status"], "dry_run")
        self.assertFalse(self.channel.harvests.exists())
        self.assertFalse(DataSnapshot.objects.exists())

    def test_manifest_drift_or_page_budget_failure_preserves_cursor(self):
        advance(self.channel, Mock(), False)
        h = self.channel.harvests.get()
        original = (h.records, h.next_url, h.size_bytes)
        self.manifest.return_value = {**MANIFEST, "scope_sha256": "b" * 64}
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), False)
        self.manifest.return_value = MANIFEST
        self.page.side_effect = FetchError("Response exceeds size/time budget")
        self.assertEqual(advance(self.channel, Mock(), False)["status"], "blocked")
        h.refresh_from_db()
        self.assertEqual((h.records, h.next_url, h.size_bytes), original)
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_blocked_same_id_on_demand_resumes_without_automatic_retry(self):
        self.page.side_effect = FetchError("Response exceeds size/time budget")
        result = advance(self.channel, Mock(), False)
        self.assertTrue(result["on_demand_required"])
        h = self.channel.harvests.get()
        self.assertEqual((h.records, h.next_url, h.status), (0, page_url(1), "blocked"))
        self.manifest.reset_mock()
        self.page.reset_mock()
        self.assertEqual(advance(self.channel, Mock(), False)["status"], "blocked")
        self.manifest.assert_not_called()
        self.page.assert_not_called()
        self.page.side_effect = lambda client, expected, **kw: (RAW, page_url(expected["id"]))
        self.assertEqual(advance(self.channel, Mock(), False, on_demand=True)["records"], 1)
        self.page.assert_called_once_with(
            self.page.call_args.args[0], MANIFEST["identity_manifest"][0], on_demand=True
        )
        h.refresh_from_db()
        self.assertEqual((h.status, h.error, h.next_url), ("running", "", page_url(2)))
        self.assertTrue(DataSnapshot.objects.get().summary["on_demand"])

    def test_budget_dry_run_no_diagnostic_writes_and_other_errors_not_reclassified(self):
        self.page.side_effect = FetchError("Response exceeds size/time budget")
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), True, on_demand=True)
        self.assertFalse(self.channel.harvests.exists())
        self.page.side_effect = FetchError("BDL scan pinned identity/year changed")
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), False)
        self.assertFalse(self.channel.harvests.exists())

    def test_budget_diagnostic_refuses_revoked_approval(self):
        def revoke(client, expected):
            DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
            raise FetchError("Response exceeds size/time budget")

        self.page.side_effect = revoke
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), False)
        self.assertFalse(self.channel.harvests.exists())

    @override_settings(DATA_MANUAL_MAX_BYTES=80 * 1024 * 1024, DATA_AUTO_MAX_BYTES=1024)
    def test_manual_ceiling_cannot_raise_automatic_or_exceed_50mib(self):
        self.assertEqual(page_budget(False), 1024)
        self.assertEqual(page_budget(True), 50 * 1024 * 1024)

    def test_on_demand_cli_requires_fetch(self):
        with self.assertRaises(CommandError):
            call_command("bdl_scan", on_demand=True, stdout=io.StringIO())

    def test_blocked_status_and_same_source_url_visible_without_fetch(self):
        self.page.side_effect = FetchError("Response exceeds size/time budget")
        advance(self.channel, Mock(), False)
        with patch("ingestion.transport.Client.get") as get:
            response = self.client.get(reverse("data_detail", args=[self.channel.pk]))
            self.assertContains(response, "Obiekt nie został pominięty")
            self.assertContains(response, "maksymalnie 50 MiB")
            self.assertContains(response, "id=1")
            get.assert_not_called()

    def test_change_during_request_and_revoked_approval_no_write(self):
        self.manifest.side_effect = [MANIFEST, {**MANIFEST, "scope_sha256": "b" * 64}]
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), False)
        self.assertFalse(self.channel.harvests.exists())
        self.manifest.side_effect = None

        def revoke(client, expected):
            DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
            return RAW, page_url(1)

        self.page.side_effect = revoke
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), False)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_snapshot_failure_rolls_back_new_harvest(self):
        with patch("ingestion.bdl_scan.DataSnapshot.objects.create", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                advance(self.channel, Mock(), False)
        self.assertFalse(self.channel.harvests.exists())

    @override_settings(DATA_STORAGE_MAX_BYTES=RESERVE)
    def test_cache_full_refuses_before_http(self):
        with self.assertRaises(FetchError):
            advance(self.channel, Mock(), False)
        self.manifest.assert_not_called()

    def test_commands_plan_prepare_preserve_profile_and_no_generic_enable(self):
        with patch("ingestion.transport.Client.get") as get:
            call_command("bdl_scan", stdout=io.StringIO())
            call_command("bdl_scan", prepare=True, apply=True, stdout=io.StringIO())
            get.assert_not_called()
        self.channel.refresh_from_db()
        self.assertFalse(self.channel.enabled)
        self.assertTrue(self.channel.access_approved)
        with self.assertRaises(CommandError):
            call_command("audit_data", source=SLUG, enable=True, stdout=io.StringIO())

    def test_real_page_validation_pinned_id_year_and_links(self):
        self.page.stop()
        data = {
            "type": "FeatureCollection",
            "numberMatched": 1,
            "numberReturned": 1,
            "links": [{"rel": "self", "href": page_url(1)}],
            "features": [
                {
                    "type": "Feature",
                    "id": 1,
                    "properties": {"a_year": 2026},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[14, 50], [15, 50], [15, 51], [14, 50]]],
                    },
                }
            ],
        }
        client = Mock(max_bytes=5 * 1024 * 1024)

        def respond(value):
            client.get.return_value = Response(200, json.dumps(value).encode(), {}, page_url(1))

        respond(data)
        self.assertEqual(fetch_page(client, MANIFEST["identity_manifest"][0])[1], page_url(1))
        large = deepcopy(data)
        large["padding"] = "x" * (6 * 1024 * 1024)
        respond(large)
        with self.assertRaises(FetchError):
            fetch_page(client, MANIFEST["identity_manifest"][0])
        self.assertEqual(fetch_page(client, MANIFEST["identity_manifest"][0], True)[1], page_url(1))
        for mutation in (
            lambda d: d.update(numberMatched=True),
            lambda d: d["features"][0].update(id=2),
            lambda d: d["features"][0]["properties"].update(a_year=2025),
            lambda d: d["features"][0].update(geometry=None),
            lambda d: d["links"].append({"rel": "next", "href": page_url(2)}),
        ):
            changed = deepcopy(data)
            mutation(changed)
            respond(changed)
            with self.assertRaises(FetchError):
                fetch_page(client, MANIFEST["identity_manifest"][0])
