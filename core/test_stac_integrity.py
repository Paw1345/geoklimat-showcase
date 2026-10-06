import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from core.test_stac_coverage import StacCoverageTests
from ingestion.models import DataSnapshot
from ingestion.stac_integrity import verify_batch
from ingestion.transport import FetchError


class StacIntegrityTests(TransactionTestCase):
    def setUp(self):
        StacCoverageTests.setUp(self)
        self.channel.access_approved = True
        self.channel.save(update_fields=["access_approved"])

    def persist(self, page=1, ids=("scene-a", "scene-b"), change=None):
        features = []
        stamps = []
        for index, identity in enumerate(ids):
            stamp = self.now - timedelta(days=2 - index)
            stamps.append(stamp)
            features.append(
                {
                    "type": "Feature",
                    "id": identity,
                    "collection": "sentinel-2-l2a",
                    "properties": {"datetime": stamp.isoformat()},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                    },
                    "assets": {"fixture": {"href": "https://example.org/do-not-export-url"}},
                }
            )
        data = {"type": "FeatureCollection", "features": features}
        if change:
            change(data)
        raw = json.dumps(data).encode()
        summary = {
            "kind": "stac",
            "scope": "PL",
            "bbox": self.channel.config["bbox"],
            "harvest_id": self.harvest.pk,
            "page": page,
            "records": len(ids),
            "raw_records": len(ids),
            "request_url": self.start_url + ("&token=fixture" if page > 1 else ""),
            "assets_downloaded": False,
        }
        if stamps:
            summary.update(
                oldest_observation=min(stamps).isoformat(),
                newest_observation=max(stamps).isoformat(),
            )
        return DataSnapshot.objects.create(
            channel=self.channel,
            fetched_at=self.now,
            expires_at=self.now + timedelta(days=30),
            size_bytes=len(raw),
            payload=raw,
            sha256=hashlib.sha256(raw).hexdigest(),
            media_type="application/json",
            summary=summary,
        )

    def test_full_batch_hash_schema_dates_geometry_and_receipts(self):
        snapshot = self.persist()
        result = verify_batch(self.channel, self.harvest, self.now)
        self.assertTrue(result["batch_verified"])
        self.assertFalse(result["all_harvest_pages_verified"])
        self.assertFalse(result["full_current_30_day_coverage_verified"])
        self.assertEqual(result["distinct_scene_ids_in_batch"], 2)
        self.assertEqual(result["receipts"][0]["sha256"], snapshot.sha256)
        self.assertNotIn("do-not-export-url", json.dumps(result, default=str))

    def test_sha_corruption_same_length_rejected(self):
        snapshot = self.persist()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(payload=b"!" * snapshot.size_bytes)
        with self.assertRaisesRegex(FetchError, "SHA256"):
            verify_batch(self.channel, self.harvest, self.now)

    def test_actual_size_preflight_before_payload_read(self):
        snapshot = self.persist()
        snapshot.size_bytes = 1
        snapshot.save(update_fields=["size_bytes"])
        with patch.object(
            DataSnapshot.objects, "only", side_effect=AssertionError("No payload read")
        ):
            with self.assertRaises(FetchError):
                verify_batch(self.channel, self.harvest, self.now)

    @override_settings(DATA_MANUAL_MAX_BYTES=1)
    def test_budget_preflight_before_payload_read(self):
        self.persist()
        with patch.object(
            DataSnapshot.objects, "only", side_effect=AssertionError("No payload read")
        ):
            with self.assertRaises(FetchError):
                verify_batch(self.channel, self.harvest, self.now)

    def test_dates_metadata_and_collection_mismatch(self):
        snapshot = self.persist(change=lambda data: data["features"][0].update(collection="wrong"))
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)
        snapshot.delete()
        snapshot = self.persist()
        snapshot.summary["oldest_observation"] = (self.now - timedelta(days=3)).isoformat()
        snapshot.save(update_fields=["summary"])
        with self.assertRaisesRegex(FetchError, "bounds"):
            verify_batch(self.channel, self.harvest, self.now)

    def test_duplicates_across_pages_are_reported_not_global_uniqueness(self):
        self.persist()
        self.persist(page=2)
        self.harvest.pages = 2
        self.harvest.records = 4
        result = verify_batch(self.channel, self.harvest, self.now)
        self.assertEqual(result["repeated_occurrences_in_batch"], 2)
        self.assertEqual(result["scene_ids_with_multiple_variants_in_batch"], 0)
        self.assertFalse(result["all_harvest_pages_verified"])

    def test_missing_duplicate_or_expired_pages_refused(self):
        snapshot = self.persist()
        self.harvest.pages = 2
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)
        self.harvest.pages = 1
        self.persist()
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)
        DataSnapshot.objects.exclude(pk=snapshot.pk).delete()
        snapshot.expires_at = self.now
        snapshot.save(update_fields=["expires_at"])
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)

    def test_invalid_geometry_and_duplicate_json_keys(self):
        snapshot = self.persist(change=lambda data: data["features"][0].update(geometry=None))
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)
        raw = b'{"type":"FeatureCollection","type":"FeatureCollection","features":[]}'
        snapshot.payload = raw
        snapshot.size_bytes = len(raw)
        snapshot.sha256 = hashlib.sha256(raw).hexdigest()
        snapshot.save(update_fields=["payload", "size_bytes", "sha256"])
        with self.assertRaisesRegex(FetchError, "duplicate JSON"):
            verify_batch(self.channel, self.harvest, self.now)

    def test_cli_pinned_scope_and_no_http(self):
        self.persist()
        stream = io.StringIO()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command(
                "stac_coverage",
                verify=True,
                channel=self.channel.pk,
                harvest=self.harvest.pk,
                stdout=stream,
            )
        self.assertTrue(json.loads(stream.getvalue())["verification"]["batch_verified"])
        with self.assertRaises(CommandError):
            call_command("stac_coverage", verify=True, stdout=io.StringIO())
        with self.assertRaises(CommandError):
            call_command("stac_coverage", channel=self.channel.pk, stdout=io.StringIO())
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_web_pinned_scope_permissions_budget_and_stale_harvest(self):
        self.persist()
        self.client.force_login(
            get_user_model().objects.create_user(username="staff", is_staff=True)
        )
        query = f"/data/stac/?verify=1&channel={self.channel.pk}&harvest={self.harvest.pk}"
        response = self.client.get(query + "&format=json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertTrue(response.json()["verification"]["batch_verified"])
        self.assertContains(self.client.get(query), "Zweryfikowana paczka")
        self.assertEqual(self.client.get(query + "&start=0").status_code, 400)
        self.assertEqual(self.client.get("/data/stac/?verify=1").status_code, 400)
        with override_settings(DATA_MANUAL_MAX_BYTES=1):
            self.assertEqual(self.client.get(query).status_code, 409)
        self.channel.access_approved = False
        self.channel.save(update_fields=["access_approved"])
        self.assertEqual(self.client.get(query).status_code, 409)

    def test_variant_counts_and_batch_limit(self):
        self.persist()
        self.persist(
            page=2, change=lambda data: data["features"][0]["properties"].update(extra="variant")
        )
        self.harvest.pages = 2
        result = verify_batch(self.channel, self.harvest, self.now)
        self.assertEqual(result["scene_ids_with_multiple_variants_in_batch"], 1)
        self.harvest.pages = 10001
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)

    def test_twenty_five_page_boundary_and_continuation(self):
        for page in range(1, 27):
            self.persist(page=page)
        self.harvest.pages = 26
        first = verify_batch(self.channel, self.harvest, self.now)
        self.assertEqual(first["checked_pages"], 25)
        self.assertEqual(first["next_start_page"], 26)
        self.assertEqual([r["page"] for r in first["receipts"]], list(range(1, 26)))
        last = verify_batch(self.channel, self.harvest, self.now, start=26)
        self.assertEqual(last["checked_pages"], 1)
        self.assertIsNone(last["next_start_page"])
        self.assertFalse(last["all_harvest_pages_verified"])

    def test_observation_date_must_match_frozen_and_current_window(self):
        snapshot = self.persist(
            change=lambda data: data["features"][0]["properties"].update(
                datetime=(self.now - timedelta(days=31)).isoformat()
            )
        )
        with self.assertRaisesRegex(FetchError, "outside retained scope"):
            verify_batch(self.channel, self.harvest, self.now)
        snapshot.delete()
        self.persist(
            change=lambda data: data["features"][0]["properties"].update(datetime="2026-01-01")
        )
        with self.assertRaises(FetchError):
            verify_batch(self.channel, self.harvest, self.now)
