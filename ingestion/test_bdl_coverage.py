import hashlib
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ingestion.bdl_coverage import geometry_metadata
from ingestion.bdl_paging import channel_url, config, fetch_page
from ingestion.data_coverage import coverage_report
from ingestion.data_paging import digest
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.spatial_query import query
from ingestion.transport import FetchError, Response


class BdlCoverageTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release, registry_id=39, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="bdl-lesnictwa-scan",
            name="Leśnictwa BDL",
            kind="geojson",
            region="PL",
            url=channel_url("lesnictwa"),
            config=config("lesnictwa"),
            access_approved=True,
        )
        self.now = timezone.now()
        scope = digest([self.channel.url, "geojson", "PL", self.channel.config, 6, 2026])
        self.harvest = DataHarvest.objects.create(
            channel=self.channel,
            config_hash=scope,
            status="complete",
            pages=2,
            records=6,
            start_url="",
            next_url="",
        )
        for page, ids in [(1, range(1, 6)), (2, [6])]:
            data = {
                "type": "FeatureCollection",
                "links": [],
                "features": [
                    {
                        "type": "Feature",
                        "id": i,
                        "properties": {"a_year": 2026},
                        "geometry": None
                        if i == 2
                        else {
                            "type": "Polygon",
                            "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                        },
                    }
                    for i in ids
                ],
            }
            raw = json.dumps(data).encode()
            client = Mock(max_bytes=5 * 1024 * 1024)
            client.get.side_effect = lambda url, raw=raw: Response(200, raw, {}, url)
            _, _, summary = fetch_page(
                self.channel,
                client,
                (page - 1) * 5,
                {"count": 6, "source_year": 2026, "scope": scope},
            )
            summary.update(page=page, harvest_id=self.harvest.pk)
            if page == 2:
                summary = {
                    k: v
                    for k, v in summary.items()
                    if k
                    not in {
                        "geometry_policy",
                        "geometry_requested",
                        "geometry_present_records",
                        "geometry_missing_records",
                        "geometry_missing_ids",
                    }
                }
            DataSnapshot.objects.create(
                channel=self.channel,
                summary=summary,
                payload=raw,
                size_bytes=len(raw),
                media_type="application/geo+json",
                sha256=hashlib.sha256(raw).hexdigest(),
                fetched_at=self.now,
                expires_at=self.now + timedelta(days=30),
            )

    def test_metadata_counts_known_missing_and_legacy_unknown_without_payload(self):
        with CaptureQueriesContext(connection) as queries:
            result = coverage_report(self.harvest, self.now)
        self.assertFalse(any('"payload"' in q["sql"] for q in queries))
        self.assertTrue(result["full_metadata_coverage"])
        self.assertEqual(result["geometry"]["present_records"], 4)
        self.assertEqual(result["geometry"]["missing_records"], 1)
        self.assertEqual(result["geometry"]["unknown_records"], 1)
        self.assertEqual(result["geometry"]["status"], "warn")
        self.assertFalse(result["geometry"]["full_geometry_coverage"])
        self.assertFalse(result["export_eligible"])

    def test_search_skips_null_and_verifies_original_bdl_pages(self):
        for method in ("envelope", "geometry"):
            with (
                self.subTest(method=method),
                patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")),
            ):
                result, _, _ = query(self.channel.pk, [14, 49, 17, 52], method=method)
                self.assertEqual([f["id"] for f in result["features"]], [1, 3, 4, 5, 6])
                self.assertEqual(result["geoklimat_query"]["missing_geometry_records"], 1)
                self.assertFalse(result["geoklimat_query"]["full_scope_verified"])
                self.assertTrue(result["geoklimat_query"]["full_record_scope_verified"])
        self.assertEqual(DataSnapshot.objects.count(), 2)
        self.assertIsNone(
            json.loads(bytes(DataSnapshot.objects.order_by("pk").first().payload))["features"][1][
                "geometry"
            ]
        )

    def test_batched_search_also_reports_missing_geometry(self):
        result, _, _ = query(self.channel.pk, [14, 49, 17, 52], copy_page=1, limit=2)
        self.assertEqual(result["geoklimat_query"]["missing_geometry_records"], 1)
        self.assertEqual(result["geoklimat_query"]["returned"], 2)
        self.assertIsNone(result["geoklimat_query"]["global_match_count"])
        self.assertEqual(result["geoklimat_query"]["result_pagination"]["order"], "id_ascending")

    def test_forged_geometry_counts_refuse_payload_verification(self):
        copy = DataSnapshot.objects.order_by("pk").first()
        copy.summary.update(
            geometry_missing_records=0,
            geometry_missing_ids=[],
            geometry_present_records=5,
            geometry_included=True,
        )
        copy.save(update_fields=["summary"])
        with self.assertRaises(FetchError):
            query(self.channel.pk, [14, 49, 17, 52])

    def test_channel_page_explains_geometry_independently_of_record_completion(self):
        response = self.client.get(f"/data/{self.channel.pk}/")
        self.assertContains(response, "Braki geometrii: 1")
        self.assertContains(response, "Niepotwierdzone rekordy: 1")
        self.assertContains(response, "Ukończony przebieg")

    def test_all_legacy_metadata_remains_unknown(self):
        copy = DataSnapshot.objects.order_by("pk").first()
        copy.summary = {k: v for k, v in copy.summary.items() if not k.startswith("geometry_")}
        copy.save(update_fields=["summary"])
        result = coverage_report(self.harvest, self.now)
        self.assertEqual(result["geometry"]["unknown_records"], 6)
        self.assertEqual(result["geometry"]["status"], "unconfirmed")
        with self.assertRaises(FetchError):
            query(self.channel.pk, [14, 49, 17, 52])

    def test_dashboard_does_not_hide_geometry_warning_behind_completed_scan(self):
        response = self.client.get("/data/coverage/")
        self.assertContains(response, "Przebieg ukończony, wszystkie kopie niewygasłe")
        self.assertContains(response, "Geometria: warn.")
        self.assertContains(response, "Braki geometrii: 1; niepotwierdzone rekordy: 1.")

    def test_expired_geometry_not_counted_as_available(self):
        DataSnapshot.objects.filter(summary__page=1).update(expires_at=self.now)
        result = coverage_report(self.harvest, self.now)
        self.assertEqual(result["available_records"], 1)
        self.assertEqual(result["geometry"]["missing_records"], 0)
        self.assertEqual(result["geometry"]["unknown_records"], 1)
        self.assertFalse(result["geometry"]["full_geometry_coverage"])

    def test_invalid_geometry_metadata_does_not_claim_coverage(self):
        copy = DataSnapshot.objects.order_by("pk").first()
        copy.summary["geometry_missing_ids"] = [False]
        copy.save(update_fields=["summary"])
        result = coverage_report(self.harvest, self.now)
        self.assertIn("invalid_geometry_metadata", result["issues"])
        self.assertFalse(result["full_metadata_coverage"])

    def test_changed_payload_year_refused_even_with_matching_checksum(self):
        copy = DataSnapshot.objects.order_by("pk").first()
        data = json.loads(bytes(copy.payload))
        data["features"][0]["properties"]["a_year"] = 2025
        copy.payload = json.dumps(data).encode()
        copy.sha256 = hashlib.sha256(copy.payload).hexdigest()
        copy.size_bytes = len(copy.payload)
        copy.save()
        with self.assertRaises(FetchError):
            query(self.channel.pk, [14, 49, 17, 52])

    def test_spatial_html_explains_missing_geometries_and_preserves_original_export(self):
        user = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.client.force_login(user)
        response = self.client.get(
            "/data/spatial/",
            {
                "channel": self.channel.pk,
                "west": 14,
                "south": 49,
                "east": 17,
                "north": 52,
                "limit": 10,
                "method": "geometry",
                "format": "html",
            },
        )
        self.assertContains(response, "pominięto 1 rekordów")
        copy = DataSnapshot.objects.order_by("pk").first()
        response = self.client.get(f"/data/snapshots/{copy.pk}/download/")
        self.assertEqual(response.status_code, 200)
        raw = b"".join(response.streaming_content) if response.streaming else response.content
        self.assertEqual(raw, bytes(copy.payload))

    def test_out_of_range_cursor_stops_before_http(self):
        client = Mock(max_bytes=5 * 1024 * 1024)
        with self.assertRaises(FetchError):
            fetch_page(self.channel, client, 6, {"count": 6})
        client.get.assert_not_called()

    def test_complete_geometry_requires_confirmed_counts_and_full_copy_scope(self):
        rows = [
            {
                "summary": {
                    "records": 6,
                    "first_id": 1,
                    "geometry_policy": "bdl-null-reported-v1",
                    "geometry_requested": True,
                    "geometry_present_records": 6,
                    "geometry_missing_records": 0,
                    "geometry_missing_ids": [],
                    "geometry_included": True,
                }
            }
        ]
        self.assertEqual(geometry_metadata(rows, True, True)["status"], "complete")
        self.assertEqual(geometry_metadata(rows, True, False)["status"], "unconfirmed")

    def test_attribute_scan_never_claims_full_geometry(self):
        result = geometry_metadata([], False, True)
        self.assertEqual(result["status"], "not_requested")
        self.assertFalse(result["full_geometry_coverage"])
