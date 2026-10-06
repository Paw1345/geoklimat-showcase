import hashlib
import io
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ingestion.data_coverage import coverage_report
from ingestion.data_paging import digest
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.pig_data import channel_url
from ingestion.pig_paging import config


class CoverageTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release, registry_id=65, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="pig-midas-0-scan",
            url=channel_url(0),
            kind="geojson",
            region="PL",
            config=config(0),
        )
        self.now = timezone.now()
        self.scope = digest([self.channel.url, "geojson", "PL", config(0), [1, 2, 3, 4]])
        self.harvest = DataHarvest.objects.create(
            channel=self.channel,
            config_hash=self.scope,
            status="complete",
            pages=2,
            records=4,
            next_url="",
        )
        for page, ids in [(1, [1, 2]), (2, [3, 4])]:
            summary = {
                "harvest_id": self.harvest.pk,
                "collector": "pig_midas_scan",
                "identity_scope_hash": self.scope,
                "page": page,
                "records": 2,
                "source_count": 4,
            }
            payload = json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": i,
                            "properties": {"OBJECTID": i},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                            },
                        }
                        for i in ids
                    ],
                    "geoklimat_provenance": {"identity_scope_hash": self.scope, "source_count": 4},
                }
            ).encode()
            DataSnapshot.objects.create(
                channel=self.channel,
                summary=summary,
                payload=payload,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                media_type="application/json",
                fetched_at=self.now,
                expires_at=self.now + timedelta(days=30),
            )

    def test_full_metadata_and_payload_verification_are_distinct(self):
        report = coverage_report(self.harvest, self.now)
        self.assertTrue(report["full_metadata_coverage"])
        self.assertEqual(report["payload_check"], "not_checked")
        report = coverage_report(self.harvest, self.now, True)
        self.assertTrue(report["scope_verified"])
        self.assertEqual(report["available_records"], 4)

    def test_expiry_deleted_page_and_hard_retention(self):
        s = DataSnapshot.objects.first()
        s.expires_at = self.now
        s.save()
        report = coverage_report(self.harvest, self.now, True)
        self.assertTrue(report["scan_complete"])
        self.assertFalse(report["full_metadata_coverage"])
        self.assertFalse(report["scope_verified"])
        self.assertEqual(report["available_records"], 2)
        s.delete()
        self.assertEqual(coverage_report(self.harvest, self.now)["unavailable_pages"], 1)
        DataSnapshot.objects.update(
            fetched_at=self.now - timedelta(days=31), expires_at=self.now + timedelta(days=1)
        )
        self.assertEqual(coverage_report(self.harvest, self.now)["available_pages"], 0)

    def test_all_copies_gone_does_not_invent_source_count(self):
        DataSnapshot.objects.all().delete()
        report = coverage_report(self.harvest, self.now, True)
        self.assertIsNone(report["source_count"])
        self.assertEqual(report["payload_check"], "no_available_pages")
        self.assertFalse(report["scope_verified"])

    def test_wrong_scope_duplicate_and_running_are_not_full(self):
        s = DataSnapshot.objects.first()
        s.summary["page"] = 1
        s.save()
        self.assertIn("invalid_metadata", coverage_report(self.harvest, self.now)["issues"])
        s.summary["identity_scope_hash"] = "b" * 64
        s.save()
        self.assertFalse(coverage_report(self.harvest, self.now)["full_metadata_coverage"])
        self.harvest.status = "running"
        self.assertFalse(coverage_report(self.harvest, self.now)["scan_complete"])

    def test_payload_corruption_and_scope_hash_fail_closed(self):
        s = DataSnapshot.objects.first()
        s.payload = b"{}"
        s.save()
        report = coverage_report(self.harvest, self.now, True)
        self.assertTrue(report["full_metadata_coverage"])
        self.assertEqual(report["payload_check"], "failed")
        self.assertFalse(report["scope_verified"])

    def test_read_only_command_and_ui_do_not_modify_copies(self):
        before = list(DataSnapshot.objects.values())
        out = io.StringIO()
        call_command("data_coverage", source=self.channel.slug, verify_payloads=True, stdout=out)
        self.assertTrue(json.loads(out.getvalue())[0]["scope_verified"])
        response = self.client.get("/data/" + str(self.channel.pk) + "/")
        self.assertContains(response, "Kompletność i dostępność kopii")
        self.assertContains(response, "nie potwierdza ich integralności")
        self.assertEqual(before, list(DataSnapshot.objects.values()))

    def test_dashboard_is_read_only_and_disallows_post(self):
        self.assertContains(self.client.get("/data/coverage/"), "wszystkie kopie niewygasłe")
        self.assertEqual(self.client.post("/data/coverage/").status_code, 405)

    def test_required_verification_checks_payloads_without_separate_flag(self):
        before = list(DataSnapshot.objects.values())
        out = io.StringIO()
        call_command("data_coverage", source=self.channel.slug, require_verified=True, stdout=out)
        self.assertTrue(json.loads(out.getvalue())[0]["scope_verified"])
        self.assertEqual(before, list(DataSnapshot.objects.values()))

    def test_required_verification_fails_with_json_for_corrupt_or_incomplete_scan(self):
        snapshot = DataSnapshot.objects.first()
        snapshot.payload = b"{}"
        snapshot.save()
        for state in ("complete", "running"):
            self.harvest.status = state
            self.harvest.save()
            out = io.StringIO()
            with self.assertRaisesMessage(CommandError, "Full verified coverage is unavailable"):
                call_command("data_coverage", require_verified=True, stdout=out)
            self.assertFalse(json.loads(out.getvalue())[0]["scope_verified"])
        out = io.StringIO()
        call_command("data_coverage", verify_payloads=True, stdout=out)
        self.assertFalse(json.loads(out.getvalue())[0]["scope_verified"])

    def test_required_verification_fails_for_not_started_or_empty_selection(self):
        self.harvest.delete()
        for empty in (False, True):
            if empty:
                self.channel.config = {}
                self.channel.save()
            out = io.StringIO()
            with self.assertRaisesMessage(CommandError, "Full verified coverage is unavailable"):
                call_command("data_coverage", require_verified=True, stdout=out)
            self.assertEqual(
                json.loads(out.getvalue()),
                [] if empty else [{"channel": self.channel.slug, "status": "not_started"}],
            )

    def test_current_profile_change_cannot_verify_historical_scope(self):
        self.channel.config = {**self.channel.config, "admin_change": True}
        self.channel.save()
        self.harvest.channel = self.channel
        report = coverage_report(self.harvest, self.now, True)
        self.assertEqual(report["payload_check"], "failed")
        self.assertFalse(report["scope_verified"])

    def test_combined_export_staff_only_with_actual_byte_etag(self):
        url = "/data/harvests/" + str(self.harvest.pk) + "/download/"
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create(username="reviewer", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.post(url).status_code, 405)
        before = list(DataSnapshot.objects.values())
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        raw = b"".join(response.streaming_content)
        data = json.loads(raw)
        self.assertEqual([f["id"] for f in data["features"]], [1, 2, 3, 4])
        self.assertEqual(len(data["geoklimat_provenance"]["copy_receipts"]), 2)
        self.assertFalse(data["geoklimat_provenance"]["partial"])
        self.assertEqual(response["ETag"].strip('"'), hashlib.sha256(raw).hexdigest())
        response.close()
        self.assertEqual(before, list(DataSnapshot.objects.values()))

    def test_combined_export_refuses_incomplete_expired_corrupt_and_over_budget(self):
        user = get_user_model().objects.create(username="reviewer", is_staff=True)
        self.client.force_login(user)
        url = "/data/harvests/" + str(self.harvest.pk) + "/download/"
        self.harvest.status = "running"
        self.harvest.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.harvest.status = "complete"
        self.harvest.save()
        with override_settings(DATA_MANUAL_MAX_BYTES=1):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertFalse(coverage_report(self.harvest, self.now)["export_eligible"])
            self.assertContains(
                self.client.get("/data/" + str(self.channel.pk) + "/"),
                "przekraczają limit scalonego eksportu",
            )
        s = DataSnapshot.objects.first()
        s.expires_at = self.now
        s.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        s.expires_at = self.now + timedelta(days=1)
        s.payload = b"{}"
        s.save()
        self.assertEqual(self.client.get(url).status_code, 404)
