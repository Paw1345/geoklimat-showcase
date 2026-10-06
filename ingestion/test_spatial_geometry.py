import hashlib
import json
from copy import deepcopy
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core import signing
from django.test import SimpleTestCase, TransactionTestCase

from ingestion.models import DataSnapshot
from ingestion.spatial_batches import SALT
from ingestion.spatial_geometry import GeometryFilter
from ingestion.spatial_query import query
from ingestion.test_spatial_query import SpatialTests
from ingestion.transport import FetchError


def polygon(rings):
    return {"type": "Polygon", "coordinates": rings}


OUTER = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[3, 3], [7, 3], [7, 7], [3, 7], [3, 3]]
INVALID = [[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]]


class GeometryPredicateTests(SimpleTestCase):
    def test_hole_not_a_hit_boundary_touch_is_a_hit(self):
        geometry = polygon([OUTER, HOLE])
        self.assertFalse(GeometryFilter([4, 4, 6, 6]).matches(geometry))
        self.assertTrue(GeometryFilter([2, 4, 3, 6]).matches(geometry))
        self.assertTrue(GeometryFilter([10, 4, 11, 6]).matches(geometry))

    def test_inside_crossing_containing_and_outside(self):
        geometry = polygon([OUTER])
        for bbox in ([1, 1, 2, 2], [-1, 4, 1, 6], [-1, -1, 11, 11]):
            self.assertTrue(GeometryFilter(bbox).matches(geometry))
        self.assertFalse(GeometryFilter([11, 11, 12, 12]).matches(geometry))

    def test_disjoint_multipolygon_has_no_gap_hit(self):
        geometry = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                [[[10, 10], [11, 10], [11, 11], [10, 11], [10, 10]]],
            ],
        }
        self.assertFalse(GeometryFilter([4, 4, 5, 5]).matches(geometry))
        self.assertTrue(GeometryFilter([0.2, 0.2, 0.8, 0.8]).matches(geometry))

    def test_concave_polygon_envelope_false_positive_removed(self):
        geometry = polygon([[[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10], [0, 0]]])
        self.assertFalse(GeometryFilter([4, 4, 5, 5]).matches(geometry))

    def test_invalid_self_intersection_hole_and_overlapping_parts_refused(self):
        outside_hole = [[11, 11], [12, 11], [12, 12], [11, 12], [11, 11]]
        for geometry in (
            polygon([INVALID]),
            polygon([OUTER, outside_hole]),
            {"type": "MultiPolygon", "coordinates": [[OUTER], [OUTER]]},
        ):
            before = deepcopy(geometry)
            with self.assertRaises(FetchError):
                GeometryFilter([20, 20, 21, 21]).matches(geometry)
            self.assertEqual(geometry, before)

    def test_antimeridian_not_interpreted_as_global_polygon(self):
        geometry = polygon([[[179, 0], [-179, 0], [-179, 1], [179, 1], [179, 0]]])
        with self.assertRaises(FetchError):
            GeometryFilter([0, 0, 1, 1]).matches(geometry)

    def test_query_position_budget_counts_all_features(self):
        with patch("ingestion.spatial_geometry.MAX_QUERY_POSITIONS", 9):
            selector = GeometryFilter([20, 20, 21, 21])
            self.assertFalse(selector.matches(polygon([OUTER])))
            with self.assertRaises(FetchError):
                selector.matches(polygon([OUTER]))


class GeometryQueryTests(TransactionTestCase):
    def setUp(self):
        SpatialTests.setUp(self)

    def replace_geometry(self, page, geometry):
        snapshot = DataSnapshot.objects.get(summary__page=page)
        data = json.loads(bytes(snapshot.payload))
        data["features"][0]["geometry"] = geometry
        snapshot.payload = json.dumps(data).encode()
        snapshot.size_bytes = len(snapshot.payload)
        snapshot.sha256 = hashlib.sha256(snapshot.payload).hexdigest()
        snapshot.save()

    def test_verified_predicate_receipts_no_http_or_writes(self):
        before = list(DataSnapshot.objects.values_list("pk", "sha256"))
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            result, payload, checksum = query(
                self.channel.pk, [15, 50, 16, 51], 1, method="geometry"
            )
        meta = result["geoklimat_query"]
        self.assertTrue(meta["exact_geometry_intersection"])
        self.assertEqual(meta["method"], "planar_geometry_bbox_intersection")
        self.assertEqual(meta["topology_verified_features"], 2)
        self.assertEqual(meta["geometry_positions_checked"], 10)
        self.assertFalse(meta["geodesic"])
        self.assertFalse(meta["administrative_mask_verified"])
        self.assertFalse(meta["clipped"])
        self.assertEqual(len(meta["receipts"]), 2)
        self.assertEqual(checksum, hashlib.sha256(payload).hexdigest())
        self.assertEqual(before, list(DataSnapshot.objects.values_list("pk", "sha256")))

    def test_hole_counts_differ_without_changing_original_geometry(self):
        self.replace_geometry(1, polygon([OUTER, HOLE]))
        bbox = [4, 4, 6, 6]
        self.assertEqual(query(self.channel.pk, bbox)[0]["geoklimat_query"]["returned"], 1)
        meta = query(self.channel.pk, bbox, method="geometry")[0]["geoklimat_query"]
        self.assertEqual(meta["returned"], 0)
        self.assertEqual(meta["topology_verified_features"], 2)

    def test_invalid_outside_bbox_after_limit_refuses_entire_query_and_export(self):
        self.replace_geometry(2, polygon([INVALID]))
        with self.assertRaisesRegex(FetchError, "topology invalid"):
            query(self.channel.pk, [15, 50, 16, 51], 1, method="geometry")
        self.client.force_login(self.staff)
        response = self.client.get(
            "/data/spatial/",
            {
                "channel": self.channel.pk,
                "west": 15,
                "south": 50,
                "east": 16,
                "north": 51,
                "limit": 1,
                "method": "geometry",
                "format": "geojson",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("features", response.json())
        self.assertEqual(response.json()["error_code"], "spatial_geometry_topology")

    def test_method_change_invalidates_signed_batch(self):
        first = query(self.channel.pk, [15, 50, 16, 51], 1, copy_page=1, method="geometry")[0]
        token = first["geoklimat_query"]["copy_batch"]["token"]
        with self.assertRaises(FetchError):
            query(self.channel.pk, [15, 50, 16, 51], 1, copy_page=1, copy_token=token)
        second = query(
            self.channel.pk,
            [15, 50, 16, 51],
            1,
            copy_page=1,
            copy_token=token,
            result_page=2,
            method="geometry",
        )[0]
        self.assertEqual(second["features"][0]["id"], 2)
        self.assertEqual(second["geoklimat_query"]["topology_verified_features"], 2)

    def test_legacy_v1_token_is_envelope_only(self):
        result = query(self.channel.pk, [15, 50, 16, 51], 1, copy_page=1)[0]
        values = signing.loads(result["geoklimat_query"]["copy_batch"]["token"], salt=SALT)
        values["v"] = 1
        values.pop("method")
        token = signing.dumps(values, salt=SALT)
        query(self.channel.pk, [15, 50, 16, 51], 1, copy_page=1, copy_token=token)
        with self.assertRaises(FetchError):
            query(
                self.channel.pk,
                [15, 50, 16, 51],
                1,
                copy_page=1,
                copy_token=token,
                method="geometry",
            )

    def test_bad_method_and_position_budget_fail_closed(self):
        for method in (None, [], "repair", "Geometry"):
            with self.assertRaises(FetchError):
                query(self.channel.pk, [15, 50, 16, 51], method=method)
        with patch("ingestion.spatial_geometry.MAX_QUERY_POSITIONS", 6):
            with self.assertRaises(FetchError):
                query(self.channel.pk, [15, 50, 16, 51], 1, method="geometry")

    def test_staff_html_export_and_navigation_keep_method(self):
        self.client.force_login(self.staff)
        params = {
            "channel": self.channel.pk,
            "west": 15,
            "south": 50,
            "east": 16,
            "north": 51,
            "limit": 1,
            "copy_page": 1,
            "method": "geometry",
            "format": "html",
        }
        response = self.client.get("/data/spatial/", params)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zweryfikowano topologię")
        for link in response.context["links"].values():
            self.assertEqual(parse_qs(urlsplit(link).query)["method"], ["geometry"])
        response = self.client.get("/data/spatial/", {**params, "format": "geojson"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["geoklimat_query"]["exact_geometry_intersection"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"')
        response = self.client.get("/data/spatial/", {**params, "method": "repair"})
        self.assertEqual(response.status_code, 400)
