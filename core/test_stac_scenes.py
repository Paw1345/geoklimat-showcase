import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings

from core import test_stac_integrity as fixtures
from ingestion.models import DataSnapshot
from ingestion.stac_scenes import query
from ingestion.transport import FetchError


class StacScenesTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacIntegrityTests.setUp(self)

    def persist(self, **kwargs):
        return fixtures.StacIntegrityTests.persist(self, **kwargs)

    def result(self, **kwargs):
        return query(self.channel.pk, self.harvest.pk, **kwargs)

    def login(self, staff=True):
        self.client.force_login(
            get_user_model().objects.create_user(username="fixture", is_staff=staff)
        )

    def url(self, **kwargs):
        from urllib.parse import urlencode

        return "/data/stac/scenes/?" + urlencode(
            {"channel": self.channel.pk, "harvest": self.harvest.pk, **kwargs}
        )

    def test_allowlisted_geometry_export_receipts_and_digest(self):
        snapshot = self.persist()
        result, payload, digest = self.result()
        self.assertEqual(result["type"], "FeatureCollection")
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual(result["features"][0]["geometry"]["type"], "Polygon")
        self.assertEqual(result["features"][0]["properties"]["snapshot_id"], snapshot.pk)
        self.assertEqual(
            result["geoklimat"]["verification"]["receipts"][0]["sha256"], snapshot.sha256
        )
        self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        self.assertNotIn(b"do-not-export-url", payload)
        self.assertNotIn("assets", result["features"][0])
        self.assertFalse(
            result["geoklimat"]["verification"]["full_current_30_day_coverage_verified"]
        )

    def test_scene_and_aware_date_filters(self):
        self.persist()
        result, _, _ = self.result(scene="scene-b", since=self.now - timedelta(days=1))
        self.assertEqual(result["geoklimat"]["matched_occurrences_in_batch"], 1)
        self.assertEqual(result["features"][0]["properties"]["scene_id"], "scene-b")
        result, _, _ = self.result(until=self.now - timedelta(days=2))
        self.assertEqual(result["geoklimat"]["matched_occurrences_in_batch"], 1)
        with self.assertRaises(FetchError):
            self.result(since=self.now.replace(tzinfo=None))
        with self.assertRaises(FetchError):
            self.result(since=self.now, until=self.now - timedelta(days=1))

    def test_envelope_overlap_boundary_and_empty_result(self):
        self.persist()
        result, _, _ = self.result(bbox=[16, 51, 17, 52])
        self.assertEqual(result["geoklimat"]["matched_occurrences_in_batch"], 2)
        self.assertEqual(result["geoklimat"]["spatial_method"], "geometry_envelope_overlap")
        result, _, _ = self.result(bbox=[20, 50, 21, 51])
        self.assertEqual(result["features"], [])
        self.assertTrue(result["geoklimat"]["verification"]["batch_verified"])
        for bbox in ([170, 0, -170, 1], [float("nan"), 0, 1, 2], [0, 1, 2]):
            with self.assertRaises(FetchError):
                self.result(bbox=bbox)

    def test_limit_does_not_stop_validation_of_later_corrupt_copy(self):
        self.persist()
        snapshot = self.persist(page=2)
        self.harvest.pages = 2
        self.harvest.records = 4
        self.harvest.save(update_fields=["pages", "records"])
        DataSnapshot.objects.filter(pk=snapshot.pk).update(payload=b"!" * snapshot.size_bytes)
        with self.assertRaisesRegex(FetchError, "SHA256"):
            self.result(limit=1)

    def test_duplicates_and_variants_preserved_as_occurrences(self):
        self.persist()
        self.persist(page=2)
        self.harvest.pages = 2
        self.harvest.records = 4
        self.harvest.save(update_fields=["pages", "records"])
        result, _, _ = self.result(limit=1)
        self.assertEqual(result["geoklimat"]["matched_occurrences_in_batch"], 4)
        self.assertTrue(result["geoklimat"]["truncated"])
        self.assertFalse(result["geoklimat"]["occurrences_deduplicated"])
        all_rows, _, _ = self.result(limit=250)
        self.assertEqual(len({f["id"] for f in all_rows["features"]}), 4)

    def test_output_budget_refuses_result(self):
        self.persist()
        with patch("ingestion.stac_scenes.RESULT_BYTES", 10):
            with self.assertRaisesRegex(FetchError, "output budget"):
                self.result()

    def test_read_only_transaction_and_no_http(self):
        self.persist()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.result()
        with patch("ingestion.stac_scenes.verify_batch") as verify:
            verify.side_effect = lambda *a, **k: DataSnapshot.objects.update(size_bytes=1)
            with self.assertRaises(DatabaseError):
                self.result()

    def test_html_geojson_security_headers_and_escape(self):
        self.persist(ids=("<script>alert(1)</script>", "scene-b"))
        self.login()
        html = self.client.get(self.url())
        self.assertEqual(html.status_code, 200)
        self.assertContains(html, "&lt;script&gt;")
        self.assertNotContains(html, "<script>alert(1)</script>")
        export = self.client.get(self.url(format="geojson"))
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "application/geo+json")
        self.assertEqual(export["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", export["Cache-Control"])
        self.assertEqual(export["ETag"], '"' + hashlib.sha256(export.content).hexdigest() + '"')
        self.assertIn("geoklimat-stac-scenes.geojson", export["Content-Disposition"])
        self.assertEqual(len(json.loads(export.content)["features"]), 2)

    def test_web_invalid_parameters_and_missing_scope(self):
        self.login()
        self.assertEqual(self.client.get("/data/stac/scenes/").status_code, 200)
        for params in (
            {"limit": 0},
            {"limit": 251},
            {"start": 0},
            {"format": "xml"},
            {"since": "2026-01-01"},
            {"since": "garbage"},
            {"bbox": "NaN,0,1,2"},
            {"bbox": "0,0,1"},
            {"since": "2026-10-03T00:00:00Z", "until": "2026-10-02T00:00:00Z"},
        ):
            self.assertEqual(self.client.get(self.url(**params)).status_code, 400)
        self.assertEqual(
            self.client.get(self.url(harvest=99999, format="geojson")).status_code, 409
        )

    def test_web_missing_copy_budget_and_access_refused(self):
        self.login()
        self.assertEqual(self.client.get(self.url(format="geojson")).status_code, 409)
        self.persist()
        with override_settings(DATA_MANUAL_MAX_BYTES=1):
            self.assertEqual(self.client.get(self.url(format="geojson")).status_code, 409)
        self.channel.access_approved = False
        self.channel.save(update_fields=["access_approved"])
        self.assertEqual(self.client.get(self.url()).status_code, 409)

    def test_anonymous_nonstaff_and_post_not_allowed(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.login(staff=False)
        self.assertEqual(self.client.get(self.url()).status_code, 302)
        self.client.logout()
        get_user_model().objects.all().delete()
        self.login()
        self.assertEqual(self.client.post(self.url()).status_code, 405)

    def test_next_batch_keeps_pinned_scope_and_filters(self):
        for page in range(1, 27):
            self.persist(page=page)
        self.harvest.pages = 26
        self.harvest.records = 52
        self.harvest.save(update_fields=["pages", "records"])
        self.login()
        response = self.client.get(self.url(scene="scene-b", limit=1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "start=26")
        self.assertContains(response, "scene=scene-b")
        last = self.client.get(self.url(start=26, scene="absent", format="geojson"))
        self.assertEqual(last.status_code, 200)
        self.assertEqual(json.loads(last.content)["features"], [])
