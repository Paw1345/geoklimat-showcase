import hashlib
import json
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.db import DatabaseError
from django.test import TransactionTestCase

from ingestion.data_paging import digest
from ingestion.models import DataHarvest, DataSnapshot, DownloadJob
from ingestion.spatial_query import query
from ingestion.test_spatial_query import SpatialTests
from ingestion.transport import FetchError


class SpatialResultTests(TransactionTestCase):
    bbox = [15, 50, 16, 51]

    def setUp(self):
        SpatialTests.setUp(self)
        scope = digest([self.channel.url, "geojson", "PL", self.channel.config, [1, 2, 3, 4, 5]])
        self.harvest.config_hash = scope
        self.harvest.records = 5
        self.harvest.save()
        for snapshot, identities in zip(
            DataSnapshot.objects.order_by("summary__page"), ([1, 2, 3], [4, 5]), strict=True
        ):
            data = json.loads(bytes(snapshot.payload))
            original = data["features"][0]
            data["features"] = []
            for identity in identities:
                feature = deepcopy(original)
                feature["id"] = feature["properties"]["OBJECTID"] = identity
                data["features"].append(feature)
            data["geoklimat_provenance"].update(identity_scope_hash=scope, source_count=5)
            snapshot.payload = json.dumps(data).encode()
            snapshot.sha256 = hashlib.sha256(snapshot.payload).hexdigest()
            snapshot.size_bytes = len(snapshot.payload)
            snapshot.summary.update(
                identity_scope_hash=scope, source_count=5, records=len(identities)
            )
            snapshot.save()

    def run_query(self, page=1, token="", copy_page=1, bbox=None, limit=2):
        return query(
            self.channel.pk,
            bbox or self.bbox,
            limit,
            copy_page=copy_page,
            copy_token=token,
            result_page=page,
        )

    def test_all_results_once_in_order_and_no_writes_or_http(self):
        before = (
            DataSnapshot.objects.count(),
            DataHarvest.objects.count(),
            DownloadJob.objects.count(),
        )
        first = self.run_query()[0]
        token = first["geoklimat_query"]["copy_batch"]["token"]
        identities = []
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            for page in range(1, 4):
                result, raw, checksum = self.run_query(page, token)
                meta = result["geoklimat_query"]
                pagination = meta["result_pagination"]
                identities += [feature["id"] for feature in result["features"]]
                self.assertEqual(pagination["pages"], 3)
                self.assertEqual(pagination["first"], (page - 1) * 2 + 1)
                self.assertEqual(pagination["last"], min(page * 2, 5))
                self.assertEqual(pagination["has_previous"], page > 1)
                self.assertEqual(pagination["has_next"], page < 3)
                self.assertEqual(meta["matches_in_available_copies"], 5)
                self.assertEqual(len(meta["receipts"]), 2)
                self.assertTrue(meta["full_scope_verified"])
                self.assertIsNone(meta["global_match_count"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), checksum)
        self.assertEqual(identities, [1, 2, 3, 4, 5])
        self.assertEqual(
            before,
            (
                DataSnapshot.objects.count(),
                DataHarvest.objects.count(),
                DownloadJob.objects.count(),
            ),
        )

    @patch("ingestion.spatial_batches.COPIES_PER_BATCH", 1)
    def test_copy_navigation_starts_own_result_range(self):
        first = self.run_query()[0]
        token = first["geoklimat_query"]["copy_batch"]["token"]
        last = self.run_query(2, token)[0]
        self.assertEqual([feature["id"] for feature in last["features"]], [3])
        next_copy = self.run_query(1, token, copy_page=2)[0]
        self.assertEqual([feature["id"] for feature in next_copy["features"]], [4, 5])
        self.assertEqual(next_copy["geoklimat_query"]["result_pagination"]["pages"], 1)
        self.assertFalse(next_copy["geoklimat_query"]["full_scope_verified"])
        with self.assertRaises(FetchError):
            self.run_query(2, token, copy_page=2)

    def test_page_requires_token_and_valid_range(self):
        for page in (0, -1, True, 1.5, "2", 100001, 2):
            with self.subTest(page=page), self.assertRaises(FetchError):
                self.run_query(page)
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        with self.assertRaises(FetchError):
            self.run_query(4, token)
        with self.assertRaises(FetchError):
            self.run_query(2, token, copy_page=None)

    def test_zero_matches_first_page_only(self):
        first = self.run_query(bbox=[17, 50, 18, 51])[0]
        meta = first["geoklimat_query"]
        self.assertEqual(first["features"], [])
        self.assertEqual(meta["result_pagination"]["pages"], 1)
        self.assertEqual(meta["result_pagination"]["first"], 0)
        self.assertEqual(meta["result_pagination"]["last"], 0)
        with self.assertRaises(FetchError):
            self.run_query(2, meta["copy_batch"]["token"], bbox=[17, 50, 18, 51])

    def test_skipped_copy_corruption_still_refuses_later_result_page(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        copy = DataSnapshot.objects.get(summary__page=1)
        copy.payload = bytes(copy.payload).replace(b'"id": 1', b'"id": 9')
        copy.save(update_fields=["payload"])
        with self.assertRaises(FetchError):
            self.run_query(3, token)

    def test_payload_after_result_window_still_verified(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        copy = DataSnapshot.objects.get(summary__page=2)
        copy.payload = bytes(copy.payload).replace(b'"id": 5', b'"id": 9')
        copy.save(update_fields=["payload"])
        with self.assertRaises(FetchError):
            self.run_query(2, token)

    def test_filter_limit_approval_and_budget_guards_persist(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        for kwargs in ({"limit": 1}, {"bbox": [14, 50, 16, 51]}, {"token": token + "bad"}):
            args = {"page": 2, "token": token, **kwargs}
            with self.assertRaises(FetchError):
                self.run_query(**args)
        with patch("ingestion.spatial_query.RESULT_BYTES", 100), self.assertRaises(FetchError):
            self.run_query(2, token)
        self.channel.access_approved = False
        self.channel.save(update_fields=["access_approved"])
        with self.assertRaises(FetchError):
            self.run_query(2, token)

    def test_legacy_first_window_stays_compatible(self):
        result = self.run_query(copy_page=None)[0]
        self.assertIsNone(result["geoklimat_query"]["copy_batch"])
        self.assertEqual([feature["id"] for feature in result["features"]], [1, 2])
        self.assertFalse(result["geoklimat_query"]["result_pagination"]["has_next"])

    def test_postgres_transaction_remains_read_only(self):
        def write(*args):
            DataSnapshot.objects.filter(channel=self.channel).update(size_bytes=1)
            return True

        with (
            patch("ingestion.spatial_query.overlaps", side_effect=write),
            self.assertRaises(DatabaseError),
        ):
            self.run_query()
        self.assertFalse(DataSnapshot.objects.filter(size_bytes=1).exists())

    @patch("ingestion.spatial_batches.COPIES_PER_BATCH", 1)
    def test_html_navigation_export_and_reset(self):
        self.client.force_login(self.staff)
        params = {
            "channel": self.channel.pk,
            "west": 15,
            "south": 50,
            "east": 16,
            "north": 51,
            "limit": 2,
            "format": "html",
            "copy_page": 1,
        }
        response = self.client.get("/data/spatial/", params)
        links = response.context["links"]
        later = self.client.get("/data/spatial/" + links["result_next"])
        self.assertEqual(later.status_code, 200)
        self.assertEqual(later.context["result"]["features"][0]["id"], 3)
        export = self.client.get("/data/spatial/" + later.context["links"]["geojson"])
        self.assertEqual(export.status_code, 200)
        data = json.loads(export.content)
        self.assertEqual([feature["id"] for feature in data["features"]], [3])
        self.assertEqual(data["geoklimat_query"]["result_pagination"]["page"], 2)
        self.assertEqual(export["ETag"], '"' + hashlib.sha256(export.content).hexdigest() + '"')
        self.assertIn("no-store", export["Cache-Control"])
        following = self.client.get("/data/spatial/" + later.context["links"]["next"])
        self.assertEqual(
            following.context["result"]["geoklimat_query"]["result_pagination"]["page"], 1
        )
        args = {
            key: value[0] for key, value in parse_qs(urlsplit(links["result_next"]).query).items()
        }
        reset = self.client.get("/data/spatial/", {**args, "result_page": 99, "new_scope": "1"})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.context["result"]["features"][0]["id"], 1)

    def test_bad_http_page_and_failed_export_have_no_features(self):
        self.client.force_login(self.staff)
        params = {
            "channel": self.channel.pk,
            "west": 15,
            "south": 50,
            "east": 16,
            "north": 51,
            "limit": 2,
            "format": "geojson",
            "copy_page": 1,
        }
        self.assertEqual(
            self.client.get("/data/spatial/", {**params, "result_page": 0}).status_code, 400
        )
        response = self.client.get("/data/spatial/", {**params, "result_page": 2})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("features", response.json())
