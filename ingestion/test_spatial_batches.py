import hashlib
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core import signing
from django.test import TransactionTestCase, override_settings

from ingestion.data_coverage import coverage_report
from ingestion.models import DataHarvest, DataSnapshot
from ingestion.spatial_batches import SALT
from ingestion.spatial_query import query
from ingestion.test_spatial_query import SpatialTests
from ingestion.transport import FetchError


class SpatialBatchTests(TransactionTestCase):
    setUp = SpatialTests.setUp
    bbox = [15, 50, 16, 51]

    def run_query(self, page=1, token="", bbox=None, limit=1):
        return query(self.channel.pk, bbox or self.bbox, limit, copy_page=page, copy_token=token)

    @patch("ingestion.spatial_batches.COPIES_PER_BATCH", 1)
    def test_navigation_same_inventory_no_http_or_writes(self):
        before = (DataSnapshot.objects.count(), DataHarvest.objects.count())
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            first = self.run_query()[0]
            batch = first["geoklimat_query"]["copy_batch"]
            second, payload, checksum = self.run_query(2, batch["token"])
        self.assertEqual(batch["pages"], 2)
        self.assertTrue(batch["has_next"])
        self.assertEqual(first["features"][0]["id"], 1)
        self.assertEqual(second["features"][0]["id"], 2)
        metadata = second["geoklimat_query"]
        self.assertFalse(metadata["full_scope_verified"])
        self.assertIsNone(metadata["global_match_count"])
        self.assertEqual(metadata["matches_scope"], "selected_copy_batch")
        self.assertEqual(metadata["matches_in_available_copies"], 1)
        self.assertEqual(len(metadata["receipts"]), 1)
        self.assertEqual(metadata["copy_batch"]["scope_hash"], batch["scope_hash"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), checksum)
        self.assertEqual(before, (DataSnapshot.objects.count(), DataHarvest.objects.count()))

    def test_single_batch_can_verify_entire_retained_identity_scope(self):
        metadata = self.run_query()[0]["geoklimat_query"]
        self.assertTrue(metadata["full_scope_verified"])
        self.assertTrue(metadata["copy_batch"]["all_anchored_copies_selected"])
        self.assertFalse(metadata["copy_batch"]["provider_snapshot_verified"])
        self.assertEqual(metadata["matches_in_available_copies"], 2)

    def test_byte_budget_splits_instead_of_increasing_limit(self):
        size = DataSnapshot.objects.first().size_bytes
        with override_settings(DATA_MANUAL_MAX_BYTES=size):
            with self.assertRaises(FetchError):
                query(self.channel.pk, self.bbox)
            batch = self.run_query()[0]["geoklimat_query"]["copy_batch"]
            self.assertEqual(batch["pages"], 2)
            self.assertLessEqual(batch["selected_bytes"], size)
            self.assertEqual(len(self.run_query(2, batch["token"])[0]["features"]), 1)

    def test_page_and_token_validation(self):
        for page in (0, True, 10001, 2):
            with self.subTest(page=page), self.assertRaises(FetchError):
                self.run_query(page)
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        for value in (token + "tampered", "bad", "x" * 2049):
            with self.assertRaises(FetchError):
                self.run_query(token=value)
        with self.assertRaises(FetchError):
            query(self.channel.pk, self.bbox, 1, copy_token=token)

    def test_token_filters_profile_and_budget_are_bound(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        for kwargs in ({"limit": 2}, {"bbox": [14, 50, 16, 51]}):
            with self.assertRaises(FetchError):
                self.run_query(token=token, **kwargs)
        with override_settings(DATA_MANUAL_MAX_BYTES=1000), self.assertRaises(FetchError):
            self.run_query(token=token)
        self.channel.attribution = "changed"
        self.channel.save(update_fields=["attribution"])
        with self.assertRaises(FetchError):
            self.run_query(token=token)

    def test_expired_anchor_and_malformed_signed_token(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        values = signing.loads(token, salt=SALT)
        values["anchor"] = (self.now - timedelta(hours=2)).isoformat()
        for value in (values, [], {"v": 1}):
            with self.assertRaises(FetchError):
                self.run_query(token=signing.dumps(value, salt=SALT))
        with patch("django.core.signing.time.time", return_value=self.now.timestamp() + 3700):
            with self.assertRaises(FetchError):
                self.run_query(token=token)

    @patch("ingestion.spatial_batches.COPIES_PER_BATCH", 1)
    def test_changed_unselected_metadata_and_expiry_refuse_scope(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        copy = DataSnapshot.objects.get(summary__page=2)
        copy.sha256 = "a" * 64
        copy.save(update_fields=["sha256"])
        with self.assertRaises(FetchError):
            self.run_query(token=token)
        copy.expires_at = self.now
        copy.save(update_fields=["expires_at"])
        with self.assertRaises(FetchError):
            self.run_query(token=token)

    def test_actual_size_guard_precedes_payload_verification(self):
        DataSnapshot.objects.filter(summary__page=2).update(payload=b"x" * 1000)
        with patch(
            "ingestion.data_coverage._verify", side_effect=AssertionError("No payload read")
        ):
            for page in (None, 1):
                with self.assertRaises(FetchError):
                    self.run_query(page)

    @patch("ingestion.spatial_batches.COPIES_PER_BATCH", 1)
    def test_unselected_payload_not_claimed_verified_and_fails_when_selected(self):
        copy = DataSnapshot.objects.get(summary__page=2)
        copy.payload = bytes(copy.payload).replace(b'"id": 2', b'"id": 9')
        copy.save(update_fields=["payload"])
        first = self.run_query()[0]["geoklimat_query"]
        self.assertFalse(first["full_scope_verified"])
        self.assertEqual(len(first["receipts"]), 1)
        with self.assertRaises(FetchError):
            self.run_query(2, first["copy_batch"]["token"])

    def test_later_selected_corruption_refuses_even_after_output_limit(self):
        copy = DataSnapshot.objects.get(summary__page=2)
        copy.payload = bytes(copy.payload).replace(b'"id": 2', b'"id": 9')
        copy.save(update_fields=["payload"])
        with self.assertRaises(FetchError):
            self.run_query(limit=1)

    def test_new_harvest_does_not_redirect_pinned_review(self):
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        DataHarvest.objects.create(channel=self.channel, config_hash="b" * 64)
        result = self.run_query(token=token)[0]
        self.assertEqual(result["geoklimat_query"]["copy_batch"]["total_copies"], 2)
        with self.assertRaises(FetchError):
            self.run_query()

    def test_new_backdated_copy_excluded_from_pinned_inventory(self):
        second = DataSnapshot.objects.get(summary__page=2)
        values = {
            field: getattr(second, field)
            for field in (
                "channel",
                "payload",
                "sha256",
                "size_bytes",
                "media_type",
                "fetched_at",
                "expires_at",
                "summary",
            )
        }
        second.delete()
        self.harvest.pages = self.harvest.records = 1
        self.harvest.status = "running"
        self.harvest.next_url = "https://example.test/next"
        self.harvest.save()
        token = self.run_query()[0]["geoklimat_query"]["copy_batch"]["token"]
        DataSnapshot.objects.create(**values)
        self.harvest.pages = self.harvest.records = 2
        self.harvest.status = "complete"
        self.harvest.next_url = ""
        self.harvest.save()
        metadata = self.run_query(token=token)[0]["geoklimat_query"]
        self.assertEqual(metadata["copy_batch"]["total_copies"], 1)
        self.assertEqual(len(metadata["receipts"]), 1)
        self.assertFalse(metadata["full_scope_verified"])

    def test_invalid_copy_selection_refuses(self):
        identity = DataSnapshot.objects.first().pk
        for ids in ([], [identity, identity], [True], [999999]):
            with self.assertRaises(FetchError):
                coverage_report(
                    self.harvest,
                    verify_payloads=True,
                    on_page=lambda *a: None,
                    max_payload_bytes=1000,
                    copy_ids=ids,
                )
        with self.assertRaises(FetchError):
            coverage_report(self.harvest, copy_ids=[identity])

    @patch("ingestion.spatial_batches.COPIES_PER_BATCH", 1)
    def test_html_navigation_export_and_explicit_scope_restart(self):
        self.client.force_login(self.staff)
        params = {
            "channel": self.channel.pk,
            "west": 15,
            "south": 50,
            "east": 16,
            "north": 51,
            "limit": 1,
            "format": "html",
            "copy_page": 1,
        }
        response = self.client.get("/data/spatial/", params)
        self.assertEqual(response.status_code, 200)
        links = response.context["links"]
        next_params = {
            key: values[0] for key, values in parse_qs(urlsplit(links["next"]).query).items()
        }
        self.assertEqual(self.client.get("/data/spatial/", next_params).status_code, 200)
        export = self.client.get("/data/spatial/" + links["geojson"])
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "application/geo+json")
        self.assertIn("no-store", export["Cache-Control"])
        self.assertEqual(export["ETag"], '"' + hashlib.sha256(export.content).hexdigest() + '"')
        self.assertEqual(json.loads(export.content)["geoklimat_query"]["copy_batch"]["page"], 1)
        next_params["west"] = 14
        self.assertEqual(self.client.get("/data/spatial/", next_params).status_code, 409)
        self.assertEqual(
            self.client.get("/data/spatial/", {**next_params, "new_scope": "1"}).status_code, 200
        )
        self.assertEqual(
            self.client.get("/data/spatial/", {**params, "copy_page": 0}).status_code, 400
        )

    def test_batch_output_budget_fails_closed(self):
        with patch("ingestion.spatial_query.RESULT_BYTES", 100), self.assertRaises(FetchError):
            self.run_query()
