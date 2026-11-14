import hashlib
import json
import tempfile
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TransactionTestCase

from ingestion import test_spatial_query as query_tests
from ingestion.boundary_mask import MaskError, load_mask, read_manifest
from ingestion.spatial_query import query
from ingestion.transport import FetchError


def square(west, south, east, north):
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def polygon(*rings):
    return {"type": "Polygon", "coordinates": list(rings)}


def point(x, y):
    return {"type": "Point", "coordinates": [x, y]}


# Two adjacent "countries" sharing the border x=10, an enclave hole in the first
# (like Switzerland inside the EU) and a separate island part.
WEST = polygon(square(0, 0, 10, 10), square(2, 2, 4, 4))
EAST = polygon(square(10, 0, 20, 10))
ISLAND = polygon(square(30, 0, 32, 2))


def feature(geometry, name):
    return {"type": "Feature", "properties": {"name": name}, "geometry": geometry}


class MaskFixture(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.install(
            "EU",
            {
                "type": "FeatureCollection",
                "features": [feature(WEST, "W"), feature(EAST, "E"), feature(ISLAND, "I")],
            },
        )

    def install(self, region, document, **overrides):
        raw = json.dumps(document).encode()
        name = f"{region.lower()}.geojson"
        (self.root / name).write_bytes(raw)
        entry = {
            "region": region,
            "file": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "crs": "EPSG:4326",
            "source_crs": "EPSG:4326",
            "version": "test-2026",
            "publisher": "Test publisher",
            "source": "Synthetic test mask",
            "source_url": "https://example.invalid/mask",
            "licence": "test-only",
            "attribution": "Test attribution",
            "retrieved_at": "2026-10-07T00:00:00Z",
        }
        entry.update(overrides)
        path = self.root / "manifest.json"
        masks = json.loads(path.read_text())["masks"] if path.exists() else []
        masks = [m for m in masks if m["region"] != region] + [entry]
        path.write_text(json.dumps({"masks": masks}))
        self.manifest = str(path)
        return raw

    def mask(self, region="EU"):
        return load_mask(region, self.manifest)


class RelationTests(MaskFixture):
    def test_points_inside_on_border_in_hole_and_outside(self):
        mask = self.mask()
        self.assertEqual(mask.relation(point(5, 8)), "inside")
        self.assertEqual(mask.relation(point(20, 5)), "boundary")
        self.assertEqual(mask.relation(point(3, 3)), "outside")
        self.assertEqual(mask.relation(point(2, 3)), "boundary")
        self.assertEqual(mask.relation(point(25, 5)), "outside")
        self.assertEqual(mask.relation(point(31, 1)), "inside")

    def test_internal_border_between_members_is_dissolved(self):
        mask = self.mask()
        self.assertEqual(mask.parts, 3)
        self.assertEqual(mask.relation(point(10, 5)), "inside")
        self.assertEqual(mask.relation(polygon(square(9, 5, 11, 6))), "inside")

    def test_polygons_crossing_touching_inside_hole_and_gap(self):
        mask = self.mask()
        self.assertEqual(mask.relation(polygon(square(19, 5, 21, 6))), "boundary")
        self.assertEqual(mask.relation(polygon(square(20, 5, 21, 6))), "boundary")
        self.assertEqual(mask.relation(polygon(square(2.5, 2.5, 3.5, 3.5))), "outside")
        self.assertEqual(mask.relation(polygon(square(2, 2, 4, 4))), "boundary")
        self.assertEqual(mask.relation(polygon(square(22, 0, 28, 2))), "outside")
        self.assertEqual(mask.relation(polygon(square(5, 5, 6, 6))), "inside")

    def test_includes_is_the_closed_shared_rule(self):
        mask = self.mask()
        self.assertTrue(mask.includes(point(20, 5)))
        self.assertTrue(mask.includes(point(5, 5)))
        self.assertFalse(mask.includes(point(3, 3)))

    def test_bbox_is_classified_not_converted(self):
        mask = self.mask()
        self.assertEqual(mask.bbox_relation([1, 5, 2, 6]), "inside")
        self.assertEqual(mask.bbox_relation([-1, 5, 1, 6]), "boundary")
        self.assertEqual(mask.bbox_relation([2.5, 2.5, 3.5, 3.5]), "outside")

    def test_invalid_record_refused_without_repair(self):
        mask = self.mask()
        bow = polygon([[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]])
        before = deepcopy(bow)
        with self.assertRaises(MaskError) as caught:
            mask.relation(bow)
        self.assertEqual(caught.exception.reason, "topology")
        self.assertEqual(bow, before)
        for bad in (
            point(181, 0),
            point(float("nan"), 0),
            {"type": "Point", "coordinates": [1, 2, 3]},
            {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            polygon([[179, 0], [-179, 0], [-179, 1], [179, 1], [179, 0]]),
        ):
            with self.assertRaises(MaskError):
                mask.relation(bad)

    def test_provenance_carries_version_crs_licence_and_no_repair(self):
        meta = self.mask().provenance()
        self.assertEqual(meta["crs"], "EPSG:4326")
        self.assertEqual(meta["version"], "test-2026")
        self.assertEqual(meta["rule"], "closed_mask_intersection")
        self.assertFalse(meta["repaired"])
        self.assertFalse(meta["simplified"])
        self.assertFalse(meta["geodesic"])


class LoadTests(MaskFixture):
    def test_checksum_mismatch_refused(self):
        self.install("PL", {"type": "FeatureCollection", "features": [feature(EAST, "P")]})
        path = self.root / "pl.geojson"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(MaskError) as caught:
            self.mask("PL")
        self.assertEqual(caught.exception.reason, "checksum")

    def test_invalid_mask_refused_without_repair(self):
        bow = polygon([[0, 0], [10, 10], [0, 10], [10, 0], [0, 0]])
        self.install("PL", {"type": "FeatureCollection", "features": [feature(bow, "P")]})
        with self.assertRaises(MaskError) as caught:
            self.mask("PL")
        self.assertEqual(caught.exception.reason, "topology")

    def test_manifest_requires_storage_crs_and_metadata(self):
        self.install("PL", polygon(square(0, 0, 1, 1)), crs="EPSG:2180")
        with self.assertRaises(MaskError):
            read_manifest(self.manifest)
        self.install("PL", polygon(square(0, 0, 1, 1)), licence="")
        with self.assertRaises(MaskError):
            read_manifest(self.manifest)
        self.install("PL", polygon(square(0, 0, 1, 1)), file="../pl.geojson")
        with self.assertRaises(MaskError):
            read_manifest(self.manifest)

    def test_named_crs_and_non_polygon_parts_refused(self):
        document = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:2180"}},
            "features": [feature(EAST, "P")],
        }
        self.install("PL", document)
        with self.assertRaises(MaskError) as caught:
            self.mask("PL")
        self.assertEqual(caught.exception.reason, "crs")
        self.install("PL", {"type": "FeatureCollection", "features": [feature(point(1, 1), "P")]})
        with self.assertRaises(MaskError):
            self.mask("PL")

    def test_missing_mask_and_position_budget(self):
        with self.assertRaises(MaskError) as caught:
            self.mask("PL")
        self.assertEqual(caught.exception.reason, "missing")
        with self.assertRaises(MaskError) as caught:
            load_mask("PL", self.root / "absent.json")
        self.assertEqual(caught.exception.reason, "missing")
        self.install("PL", polygon(square(40, 0, 41, 1)))
        with patch("ingestion.boundary_mask.MAX_MASK_POSITIONS", 4):
            with self.assertRaises(MaskError) as caught:
                self.mask("PL")
        self.assertEqual(caught.exception.reason, "budget")

    def test_command_reports_provenance_and_refusal(self):
        out = StringIO()
        call_command("boundary_mask", manifest=self.manifest, stdout=out)
        masks = json.loads(out.getvalue())["masks"]
        self.assertEqual([m["region"] for m in masks], ["EU"])
        self.assertEqual(masks[0]["parts"], 3)
        with self.assertRaises(CommandError):
            call_command("boundary_mask", manifest=self.manifest, region="PL")


class MaskQueryTests(TransactionTestCase):
    """Export /data/spatial/ applies the same closed-mask rule as the collectors."""

    def setUp(self):
        query_tests.SpatialTests.setUp(self)
        self.fixture = MaskFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tmp.cleanup)
        patcher = patch(
            "ingestion.boundary_mask.manifest_path",
            return_value=self.fixture.root / "manifest.json",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def pl(self, west):
        self.fixture.install("PL", polygon(square(west, 49, 24, 55)))

    def test_inside_boundary_and_outside_with_provenance(self):
        for west, returned, relation in ((14, 2, "inside"), (15.5, 2, "boundary"), (16.5, 0, None)):
            self.pl(west)
            meta = query(self.channel.pk, [10, 40, 30, 60], method="geometry_pl")[0][
                "geoklimat_query"
            ]
            self.assertEqual(meta["returned"], returned)
            self.assertTrue(meta["administrative_mask_verified"])
            self.assertEqual(meta["administrative_mask"]["region"], "PL")
            self.assertEqual(meta["method"], "planar_geometry_bbox_and_mask_intersection")
            if relation:
                self.assertEqual(meta["mask_relations_in_bbox"][relation], 2)
            else:
                self.assertEqual(meta["mask_relations_in_bbox"]["outside"], 2)

    def test_touching_mask_border_is_in_scope(self):
        self.pl(16)
        meta = query(self.channel.pk, [10, 40, 30, 60], method="geometry_pl")[0]["geoklimat_query"]
        self.assertEqual(meta["returned"], 2)
        self.assertEqual(meta["mask_relations_in_bbox"]["boundary"], 2)

    def test_token_pins_mask_file(self):
        self.pl(14)
        first = query(self.channel.pk, [10, 40, 30, 60], 1, copy_page=1, method="geometry_pl")[0]
        token = first["geoklimat_query"]["copy_batch"]["token"]
        query(
            self.channel.pk,
            [10, 40, 30, 60],
            1,
            copy_page=1,
            copy_token=token,
            method="geometry_pl",
        )
        self.pl(14.5)
        with self.assertRaisesRegex(FetchError, "token invalid"):
            query(
                self.channel.pk,
                [10, 40, 30, 60],
                1,
                copy_page=1,
                copy_token=token,
                method="geometry_pl",
            )

    def test_missing_mask_refused_in_view(self):
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
                "method": "geometry_pl",
                "format": "geojson",
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error_code"], "spatial_mask_missing")
        self.assertNotIn("features", response.json())
