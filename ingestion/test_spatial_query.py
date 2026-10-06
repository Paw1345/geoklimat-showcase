import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ingestion.data_paging import digest
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.pig_data import channel_url
from ingestion.pig_paging import config
from ingestion.spatial_query import envelope, overlaps, query, valid_bbox
from ingestion.transport import FetchError


class SpatialTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release, registry_id=65, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="pig-midas-0-scan",
            name="MIDAS",
            url=channel_url(0),
            kind="geojson",
            region="PL",
            config=config(0),
            enabled=True,
            access_approved=True,
            attribution="PIG-PIB test attribution",
        )
        self.scope = digest([self.channel.url, "geojson", "PL", config(0), [1, 2]])
        self.harvest = DataHarvest.objects.create(
            channel=self.channel,
            config_hash=self.scope,
            status="complete",
            pages=2,
            records=2,
            next_url="",
        )
        self.now = timezone.now()
        for page, identity in [(1, 1), (2, 2)]:
            data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": identity,
                        "properties": {"OBJECTID": identity},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[15, 50], [16, 50], [16, 51], [15, 51], [15, 50]],
                            ],
                        },
                    }
                ],
                "geoklimat_provenance": {"identity_scope_hash": self.scope, "source_count": 2},
            }
            payload = json.dumps(data).encode()
            DataSnapshot.objects.create(
                channel=self.channel,
                payload=payload,
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                media_type="application/json",
                fetched_at=self.now,
                expires_at=self.now + timedelta(days=30),
                summary={
                    "harvest_id": self.harvest.pk,
                    "collector": "pig_midas_scan",
                    "identity_scope_hash": self.scope,
                    "page": page,
                    "records": 1,
                    "source_count": 2,
                },
            )
        self.staff = get_user_model().objects.create_user(username="staff", is_staff=True)

    def test_verified_search_limits_provenance_and_no_http(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            result, payload, checksum = query(self.channel.pk, [15, 50, 16, 51], 1)
        self.assertEqual(len(result["features"]), 1)
        metadata = result["geoklimat_query"]
        self.assertEqual(metadata["matches_in_available_copies"], 2)
        self.assertTrue(metadata["truncated"])
        self.assertTrue(metadata["full_scope_verified"])
        self.assertFalse(metadata["exact_geometry_intersection"])
        self.assertEqual(len(metadata["receipts"]), 2)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), checksum)
        self.assertEqual(DataSnapshot.objects.count(), 2)
        self.harvest.refresh_from_db()
        self.assertEqual(self.harvest.records, 2)

    def test_no_match_and_boundary_touch(self):
        self.assertEqual(query(self.channel.pk, [17, 50, 18, 51])[0]["features"], [])
        self.assertEqual(len(query(self.channel.pk, [16, 50, 17, 51])[0]["features"]), 2)

    def test_expired_page_not_returned_partial_scope_explicit(self):
        DataSnapshot.objects.filter(summary__page=2).update(expires_at=self.now)
        result = query(self.channel.pk, [15, 50, 16, 51])[0]
        self.assertEqual(len(result["features"]), 1)
        self.assertFalse(result["geoklimat_query"]["full_scope_verified"])
        self.assertEqual(result["geoklimat_query"]["coverage"]["unavailable_pages"], 1)

    def test_corruption_after_result_limit_still_refuses_entire_query(self):
        DataSnapshot.objects.filter(summary__page=2).update(payload=b"changed")
        with self.assertRaises(FetchError):
            query(self.channel.pk, [15, 50, 16, 51], 1)

    @override_settings(DATA_MANUAL_MAX_BYTES=100)
    def test_read_budget_refuses_without_reading_payloads(self):
        with self.assertRaises(FetchError):
            query(self.channel.pk, [15, 50, 16, 51])

    def test_result_budget_fail_closed(self):
        with patch("ingestion.spatial_query.RESULT_BYTES", 100), self.assertRaises(FetchError):
            query(self.channel.pk, [15, 50, 16, 51])

    def test_bbox_nan_infinity_antimeridian_and_limit_validation(self):
        for bbox in [
            [float("nan"), 50, 16, 51],
            [15, 50, float("inf"), 51],
            [170, -5, -170, 5],
            [16, 50, 15, 51],
            [15, 50, 16, 95],
            [True, 50, 16, 51],
        ]:
            with self.assertRaises(FetchError):
                valid_bbox(bbox)
        for limit in [0, 501, True]:
            with self.assertRaises(FetchError):
                query(self.channel.pk, [15, 50, 16, 51], limit)

    def test_approval_revoked_no_scan_or_invalid_metadata(self):
        self.channel.access_approved = False
        self.channel.save()
        with self.assertRaises(FetchError):
            query(self.channel.pk, [15, 50, 16, 51])
        self.channel.access_approved = True
        self.channel.save()
        snapshot = DataSnapshot.objects.first()
        snapshot.summary["records"] = 999
        snapshot.save()
        with self.assertRaises(FetchError):
            query(self.channel.pk, [15, 50, 16, 51])

    def test_permissions_form_and_safe_geojson_export(self):
        url = "/data/spatial/"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 405)
        params = {
            "channel": self.channel.pk,
            "west": 15,
            "south": 50,
            "east": 16,
            "north": 51,
            "limit": 1,
            "format": "geojson",
        }
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/geo+json")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(json.loads(response.content)["geoklimat_query"]["returned"], 1)
        response = self.client.get(url, {**params, "west": 200})
        self.assertEqual(response.status_code, 400)

    def test_failed_export_does_not_leak_partial_data(self):
        self.client.force_login(self.staff)
        DataSnapshot.objects.filter(summary__page=2).update(payload=b"wrong")
        response = self.client.get(
            "/data/spatial/",
            {
                "channel": self.channel.pk,
                "west": 15,
                "south": 50,
                "east": 16,
                "north": 51,
                "limit": 1,
                "format": "geojson",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("features", response.json())

    def test_envelopes_not_exact_polygon_intersection(self):
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                [[[10, 10], [11, 10], [11, 11], [10, 10]]],
            ],
        }
        self.assertEqual(envelope(geometry), (0, 0, 11, 11))
        self.assertTrue(overlaps(envelope(geometry), [4, 4, 5, 5]))
