import json
import tempfile
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from ingestion.boundary_egm import EGM as EGM_META
from ingestion.boundary_mask import install, load_mask
from ingestion.boundary_sources import (
    EU27,
    EU_PARTS,
    NE_URL,
    PRG,
    check_capabilities,
    feature_url,
    fetch_naturalearth,
    fetch_prg,
    parse_features,
    parse_naturalearth,
)
from ingestion.transport import FetchError, Response

CAPS = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0">
 <wfs:FeatureTypeList>
  <wfs:FeatureType><wfs:Name>ms:A00_Granice_panstwa</wfs:Name>
   <wfs:DefaultCRS>urn:ogc:def:crs:EPSG::2180</wfs:DefaultCRS>
   <wfs:OtherCRS>urn:ogc:def:crs:EPSG::4326</wfs:OtherCRS></wfs:FeatureType>
 </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""


def pos(points, swap=True):
    return " ".join(f"{y} {x}" if swap else f"{x} {y}" for x, y in points)


def square(west, south, east, north):
    return [(west, south), (east, south), (east, north), (west, north), (west, south)]


def gml(surfaces, swap=True):
    polygons = ""
    for rings in surfaces:
        body = f"<gml:exterior><gml:LinearRing><gml:posList srsDimension='2'>{pos(rings[0], swap)}"
        body += "</gml:posList></gml:LinearRing></gml:exterior>"
        for hole in rings[1:]:
            body += "<gml:interior><gml:LinearRing><gml:posList srsDimension='2'>"
            body += pos(hole, swap) + "</gml:posList></gml:LinearRing></gml:interior>"
        polygons += f"<gml:surfaceMember><gml:Polygon>{body}</gml:Polygon></gml:surfaceMember>"
    return f"""<?xml version="1.0"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
 xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:ms="http://mapserver.gis.umn.edu/mapserver">
 <wfs:member><ms:A00_Granice_panstwa><ms:msGeometry><gml:MultiSurface>{polygons}
 </gml:MultiSurface></ms:msGeometry></ms:A00_Granice_panstwa></wfs:member>
</wfs:FeatureCollection>""".encode()


MAINLAND = [square(15, 50, 23, 54), square(18, 51, 19, 52)]
ISLAND = [square(14.2, 53.8, 14.6, 54.0)]


class ParseTests(SimpleTestCase):
    def test_lat_lon_swapped_to_geojson_with_hole_and_parts(self):
        document = parse_features(gml([MAINLAND, ISLAND]))
        feature = document["features"][0]
        self.assertEqual(feature["properties"]["axis_order_received"], "lat_lon_swapped")
        geometry = feature["geometry"]
        self.assertEqual(geometry["type"], "MultiPolygon")
        self.assertEqual(geometry["coordinates"][0][0][0], [15.0, 50.0])
        self.assertEqual(len(geometry["coordinates"][0]), 2)

    def test_lon_lat_kept(self):
        document = parse_features(gml([MAINLAND], swap=False))
        feature = document["features"][0]
        self.assertEqual(feature["properties"]["axis_order_received"], "lon_lat")
        self.assertEqual(feature["geometry"]["type"], "Polygon")

    def test_refusals_without_repair(self):
        bow = [[(15, 50), (16, 51), (15, 51), (16, 50), (15, 50)]]
        for body in (
            gml([bow]),
            gml([[square(0, 0, 1, 1)]]),
            b'<!DOCTYPE x [<!ENTITY a "b">]><x/>',
            b"<ows:ExceptionReport xmlns:ows='http://www.opengis.net/ows/1.1'/>",
            b"<wfs:FeatureCollection xmlns:wfs='http://www.opengis.net/wfs/2.0'/>",
            b"not xml",
        ):
            with self.assertRaises(FetchError):
                parse_features(body)

    def test_capabilities_require_layer_and_wgs84(self):
        self.assertEqual(check_capabilities(CAPS)["type_name"], "ms:A00_Granice_panstwa")
        with self.assertRaises(FetchError):
            check_capabilities(CAPS, "ms:A01_Granice_wojewodztw")
        with self.assertRaises(FetchError):
            check_capabilities(CAPS.replace(b"EPSG::4326", b"EPSG::3857"))

    def test_request_asks_provider_for_wgs84(self):
        self.assertIn("SRSNAME=urn%3Aogc%3Adef%3Acrs%3AEPSG%3A%3A4326", feature_url())
        self.assertIn("TYPENAMES=ms%3AA00_Granice_panstwa", feature_url())


class FakeClient:
    max_bytes = 5 * 1024 * 1024

    def __init__(self, body):
        self.body = body
        self.urls, self.budgets = [], []

    def get(self, url):
        self.urls.append(url)
        self.budgets.append(self.max_bytes)
        return Response(200, CAPS if "GetCapabilities" in url else self.body, {}, url)


class InstallTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.manifest = Path(tmp.name) / "boundaries" / "manifest.json"
        self.now = datetime(2026, 10, 7, 12, tzinfo=UTC)

    def test_fetch_uses_capabilities_then_bounded_feature_request(self):
        client = FakeClient(gml([MAINLAND]))
        document, caps = fetch_prg(client)
        self.assertIn("GetCapabilities", client.urls[0])
        self.assertIn("GetFeature", client.urls[1])
        self.assertEqual(client.budgets[1], 50 * 1024 * 1024)
        self.assertEqual(client.max_bytes, 5 * 1024 * 1024)
        self.assertEqual(len(document["features"]), 1)

    def test_dry_run_apply_and_repeat(self):
        document = parse_features(gml([MAINLAND, ISLAND]))
        meta = PRG | {"slug": "prg"}
        dry = install("PL", document, meta, self.manifest, now=self.now)
        self.assertTrue(dry["changed"])
        self.assertFalse(dry["applied"])
        self.assertFalse(self.manifest.exists())
        self.assertEqual(dry["mask"]["parts"], 1)
        applied = install("PL", document, meta, self.manifest, apply=True, now=self.now)
        self.assertTrue(applied["applied"])
        mask = load_mask("PL", self.manifest)
        self.assertEqual(mask.source_url, PRG["source_url"])
        self.assertEqual(mask.relation({"type": "Point", "coordinates": [18.5, 51.5]}), "outside")
        self.assertEqual(mask.relation({"type": "Point", "coordinates": [20, 53]}), "inside")
        repeat = install("PL", document, meta, self.manifest, apply=True, now=self.now)
        self.assertFalse(repeat["changed"])

    def test_new_version_keeps_old_file(self):
        meta = PRG | {"slug": "prg"}
        first = install(
            "PL", parse_features(gml([MAINLAND])), meta, self.manifest, apply=True, now=self.now
        )
        second = install(
            "PL",
            parse_features(gml([MAINLAND, ISLAND])),
            meta,
            self.manifest,
            apply=True,
            now=self.now,
        )
        self.assertNotEqual(first["entry"]["file"], second["entry"]["file"])
        self.assertTrue((self.manifest.parent / first["entry"]["file"]).exists())
        masks = json.loads(self.manifest.read_text())["masks"]
        self.assertEqual([m["file"] for m in masks], [second["entry"]["file"]])

    def test_command_dry_run_and_refusal(self):
        out = StringIO()
        with (
            patch("ingestion.management.commands.install_boundary_mask.Client") as client,
            patch("ingestion.boundary_mask.manifest_path", return_value=self.manifest),
        ):
            client.return_value = FakeClient(gml([MAINLAND]))
            call_command("install_boundary_mask", region="PL", source="prg", stdout=out)
            self.assertIn("Dry-run; no writes", out.getvalue())
            self.assertFalse(self.manifest.exists())
            client.return_value = FakeClient(gml([[square(0, 0, 1, 1)]]))
            with self.assertRaises(CommandError):
                call_command("install_boundary_mask", region="PL", source="prg")


def gpkg(path, rows, srs=4258, layer="PolbndA", field="ICC"):
    import sqlite3
    import struct

    from shapely import to_wkb
    from shapely.geometry import shape

    db = sqlite3.connect(path)
    db.execute("CREATE TABLE gpkg_geometry_columns (table_name, column_name, srs_id)")
    db.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?)", (layer, srs))
    db.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, "{field}" TEXT, geom BLOB)')
    for code, geometry in rows:
        blob = b"GP\x00\x01" + struct.pack("<i", srs) + to_wkb(shape(geometry))
        db.execute(f'INSERT INTO "{layer}" ("{field}", geom) VALUES (?, ?)', (code, blob))
    db.commit()
    db.close()


EU_CODES = sorted(
    {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HU", "IE"}
    | {"IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
)


def egm_rows(codes=EU_CODES):
    # Adjacent unit squares along a row; CH sits outside the EU selection.
    rows = [
        (code, {"type": "Polygon", "coordinates": [[list(p) for p in square(i, 40, i + 1, 41)]]})
        for i, code in enumerate(codes)
    ]
    rows.append(
        ("CH", {"type": "Polygon", "coordinates": [[list(p) for p in square(0, 45, 1, 46)]]})
    )
    rows.append(
        ("FR#CH", {"type": "Polygon", "coordinates": [[list(p) for p in square(2, 45, 3, 46)]]})
    )
    return rows


class EuroGlobalMapTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.file = self.dir / "egm.gpkg"
        self.manifest = self.dir / "boundaries" / "manifest.json"

    def test_selects_eu27_only_and_dissolves_shared_borders(self):
        from ingestion.boundary_egm import read_eu

        rows = egm_rows()
        pl = next(i for i, (code, _) in enumerate(rows) if code == "PL")
        left = rows[pl][1]["coordinates"][0][0][0]
        # Poland delivered as two administrative units sharing an internal border.
        rows[pl : pl + 1] = [
            ("PL", {"type": "Polygon", "coordinates": [[list(p) for p in square(w, 40, e, 41)]]})
            for w, e in ((left, left + 0.5), (left + 0.5, left + 1))
        ]
        gpkg(self.file, rows)
        document, details = read_eu(self.file)
        self.assertEqual(len(details["countries"]), 27)
        self.assertEqual((details["units"], details["features"]), (28, 27))
        self.assertEqual(details["interior_rings"], 0)
        poland = next(f for f in document["features"] if f["properties"]["ICC"] == "PL")
        self.assertEqual(poland["properties"]["units"], 2)
        self.assertEqual(poland["geometry"]["type"], "Polygon")
        meta = {"slug": "egm", "version": "EGM test", "licence": "test", **EGM_META}
        result = install("EU", document, meta, self.manifest, apply=True)
        mask = load_mask("EU", self.manifest)
        self.assertEqual(result["mask"]["parts"], 27)
        self.assertEqual(mask.geometry.geom_type, "Polygon")
        self.assertEqual(mask.relation({"type": "Point", "coordinates": [1, 40.5]}), "inside")
        self.assertEqual(mask.relation({"type": "Point", "coordinates": [0.5, 45.5]}), "outside")
        self.assertEqual(mask.relation({"type": "Point", "coordinates": [2.5, 45.5]}), "outside")

    def test_greece_as_gr_and_missing_member_refused(self):
        from ingestion.boundary_egm import read_eu

        gpkg(self.file, egm_rows([("GR" if c == "EL" else c) for c in EU_CODES]))
        self.assertEqual(len(read_eu(self.file)[1]["countries"]), 27)
        other = self.dir / "short.gpkg"
        gpkg(other, egm_rows([c for c in EU_CODES if c != "MT"]))
        with self.assertRaisesRegex(FetchError, "missing: MT"):
            read_eu(other)

    def test_wrong_crs_layer_and_header_refused(self):
        from ingestion.boundary_egm import read_eu

        gpkg(self.file, egm_rows(), srs=3035)
        with self.assertRaisesRegex(FetchError, "CRS"):
            read_eu(self.file)
        other = self.dir / "other.gpkg"
        gpkg(other, egm_rows(), layer="AdminA")
        with self.assertRaisesRegex(FetchError, "not in GeoPackage"):
            read_eu(other)
        with self.assertRaises(FetchError):
            read_eu(self.dir / "missing.gpkg")

    def test_command_requires_reviewed_licence_and_dry_runs(self):
        gpkg(self.file, egm_rows())
        with self.assertRaisesRegex(CommandError, "licence"):
            call_command("install_boundary_mask", region="EU", source="egm", file=str(self.file))
        with self.assertRaises(CommandError):
            call_command("install_boundary_mask", region="EU", source="prg")
        out = StringIO()
        with patch("ingestion.boundary_mask.manifest_path", return_value=self.manifest):
            call_command(
                "install_boundary_mask",
                region="EU",
                source="egm",
                file=str(self.file),
                mask_version="EGM test",
                licence="test",
                stdout=out,
            )
        self.assertIn("Dry-run; no writes", out.getvalue())
        self.assertFalse(self.manifest.exists())


def ne_body(codes=EU27 + EU_PARTS, extra=(), crs="urn:ogc:def:crs:OGC:1.3:CRS84"):
    features = []
    for index, code in enumerate(codes):
        west = -10 + index
        polygons = [[square(west, 40, west + 0.5, 41)]]
        if code == "NLD":
            polygons.append([square(-68.5, 12, -68, 12.5)])
        features.append(
            {
                "type": "Feature",
                "properties": {"ADM0_A3": code, "NAME": code},
                "geometry": {"type": "MultiPolygon", "coordinates": polygons},
            }
        )
    for code in extra:
        features.append(
            {
                "type": "Feature",
                "properties": {"ADM0_A3": code},
                "geometry": {"type": "Polygon", "coordinates": [square(30, 30, 31, 31)]},
            }
        )
    data = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": crs}}}
    return json.dumps(data | {"features": features}).encode()


class NaturalEarthTests(SimpleTestCase):
    def test_parse_keeps_eu27_only_and_drops_caribbean_netherlands(self):
        document = parse_naturalearth(ne_body(extra=("NOR", "GRL")))
        codes = [f["properties"]["ADM0_A3"] for f in document["features"]]
        self.assertEqual(codes, list(EU27 + EU_PARTS))
        nld = next(f for f in document["features"] if f["properties"]["ADM0_A3"] == "NLD")
        self.assertEqual(nld["properties"]["parts_left_out"], 1)
        self.assertEqual(nld["geometry"]["type"], "Polygon")
        self.assertIsNone(document.get("crs"))

    def test_refusals(self):
        with self.assertRaises(FetchError):
            parse_naturalearth(ne_body(codes=EU27))
        with self.assertRaises(FetchError):
            parse_naturalearth(ne_body(crs="urn:ogc:def:crs:EPSG::3035"))
        with self.assertRaises(FetchError):
            parse_naturalearth(ne_body(extra=("POL",)))
        with self.assertRaises(FetchError):
            parse_naturalearth(b"not json")

    def test_fetch_requests_pinned_url_with_bounded_budget(self):
        client = FakeClient(ne_body())
        document = fetch_naturalearth(client)
        self.assertEqual(client.urls, [NE_URL])
        self.assertEqual(client.budgets, [50 * 1024 * 1024])
        self.assertEqual(client.max_bytes, 5 * 1024 * 1024)
        self.assertEqual(len(document["features"]), 29)

    def test_command_eu_dry_run_apply_repeat_and_pairing(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manifest = Path(tmp.name) / "manifest.json"
        with (
            patch("ingestion.management.commands.install_boundary_mask.Client") as client,
            patch("ingestion.boundary_mask.manifest_path", return_value=manifest),
        ):
            client.return_value = FakeClient(ne_body())
            out = StringIO()
            call_command("install_boundary_mask", region="EU", source="naturalearth", stdout=out)
            self.assertIn("Dry-run; no writes", out.getvalue())
            self.assertFalse(manifest.exists())
            call_command(
                "install_boundary_mask",
                region="EU",
                source="naturalearth",
                apply=True,
                stdout=StringIO(),
            )
            mask = load_mask("EU", manifest)
            self.assertEqual(mask.parts, 29)
            self.assertEqual(
                mask.relation({"type": "Point", "coordinates": [-68.2, 12.2]}), "outside"
            )
            out = StringIO()
            call_command(
                "install_boundary_mask",
                region="EU",
                source="naturalearth",
                apply=True,
                stdout=out,
            )
            self.assertIn("Same mask already installed", out.getvalue())
            with self.assertRaises(CommandError):
                call_command("install_boundary_mask", region="EU", source="prg")
            with self.assertRaises(CommandError):
                call_command("install_boundary_mask", region="PL", source="naturalearth")
