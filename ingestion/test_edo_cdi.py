"""Synthetic metadata only; never copy or fetch a raster in tests."""

import hashlib
import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.request import BaseHandler
from urllib.response import addinfourl

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ingestion.edo_cdi import MAX_BYTES, URL, parse_description, preflight
from ingestion.transport import Client, FetchError, Response

XML = b"""<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0"
xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:gmlcov="http://www.opengis.net/gmlcov/1.0"
xmlns:swe="http://www.opengis.net/swe/2.0">
<wcs:CoverageDescription gml:id="cdiad">
<gml:boundedBy><gml:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/4326"
axisLabels="lat long" uomLabels="deg deg" srsDimension="2">
<gml:lowerCorner>50 14</gml:lowerCorner><gml:upperCorner>52 16</gml:upperCorner>
</gml:Envelope></gml:boundedBy><wcs:CoverageId>cdiad</wcs:CoverageId>
<gml:domainSet><gml:RectifiedGrid dimension="2" gml:id="test_grid">
<gml:limits><gml:GridEnvelope><gml:low>0 0</gml:low><gml:high>19 19</gml:high>
</gml:GridEnvelope></gml:limits><gml:axisLabels>long lat</gml:axisLabels>
<gml:origin><gml:Point srsName="http://www.opengis.net/def/crs/EPSG/0/4326">
<gml:pos>51.95 14.05</gml:pos></gml:Point></gml:origin>
<gml:offsetVector srsName="http://www.opengis.net/def/crs/EPSG/0/4326">0 0.1</gml:offsetVector>
<gml:offsetVector srsName="http://www.opengis.net/def/crs/EPSG/0/4326">-0.1 0</gml:offsetVector>
</gml:RectifiedGrid></gml:domainSet>
<gmlcov:rangeType><swe:DataRecord><swe:field name="band1"><swe:Quantity>
<swe:nilValues/><swe:uom code="W.m-2.Sr-1"/><swe:constraint><swe:AllowedValues>
<swe:interval>0 255</swe:interval><swe:significantFigures>3</swe:significantFigures>
</swe:AllowedValues></swe:constraint></swe:Quantity></swe:field></swe:DataRecord>
</gmlcov:rangeType><wcs:ServiceParameters><wcs:CoverageSubtype>RectifiedGridCoverage
</wcs:CoverageSubtype><wcs:nativeFormat/></wcs:ServiceParameters>
</wcs:CoverageDescription></wcs:CoverageDescriptions>"""


def response(body=XML, **kwargs):
    return Response(
        kwargs.get("status", 200),
        body,
        {"content-type": "text/xml; charset=utf-8"},
        kwargs.get("url", URL),
    )


class CdiParserTests(SimpleTestCase):
    def test_reports_raw_units_axes_and_provenance_without_values(self):
        result = parse_description(response())
        self.assertEqual(result["range"]["uom_raw"], "W.m-2.Sr-1")
        self.assertEqual(result["envelope"]["axis_labels_raw"], "lat long")
        self.assertEqual(result["grid"]["axis_labels_raw"], "long lat")
        self.assertEqual(result["grid"]["offset_vectors_raw"], ["0 0.1", "-0.1 0"])
        self.assertEqual(result["sha256"], hashlib.sha256(XML).hexdigest())
        self.assertIn("unit_mismatch", result["warnings"])
        self.assertEqual(result["semantic_status"], "unconfirmed")
        self.assertFalse(result["database_writes"])
        self.assertFalse(result["resources_downloaded"])
        self.assertIsNone(result["product_version"])

    def test_rejects_entity_declaration(self):
        with self.assertRaises(FetchError):
            parse_description(response(b'<!DOCTYPE x [<!ENTITY e "value">]>' + XML))

    def test_default_command_never_initializes_transport_or_queries_database(self):
        out = io.StringIO()
        with patch("ingestion.edo_cdi.Client", side_effect=AssertionError("unexpected HTTP")):
            call_command("edo_cdi_preflight", stdout=out)
        self.assertFalse(json.loads(out.getvalue())["fetched"])

    def test_rejects_response_status_url_media_and_byte_budget(self):
        for bad in [
            response(status=304),
            response(status=206),
            response(url=URL + "&TIME=2026-01-01"),
            response(b""),
            response(b" " * (MAX_BYTES + 1)),
            Response(200, XML, {"content-type": "text/html"}, URL),
            Response(200, XML, {}, URL),
        ]:
            with self.subTest(status=bad.status, size=len(bad.body)), self.assertRaises(FetchError):
                parse_description(bad)

    def test_rejects_xml_encoding_entities_and_node_budget(self):
        for body in [
            b"\xff",
            XML + b"\x00",
            b"<broken>",
            b'<!DOCTYPE x SYSTEM "https://example.org/x">' + XML,
            XML.replace(b"<wcs:ServiceParameters>", b"<wcs:ServiceParameters>" + b"<x/>" * 101),
        ]:
            with self.subTest(size=len(body)), self.assertRaises(FetchError):
                parse_description(response(body))

    def test_rejects_identity_namespace_and_duplicate_coverage(self):
        for body in [
            XML.replace(b'"http://www.opengis.net/wcs/2.0"', b'"http://wrong.example/wcs"'),
            XML.replace(b'gml:id="cdiad"', b'gml:id="cdirc"'),
            XML.replace(b">cdiad<", b">cdirc<"),
            XML.replace(
                b"</wcs:CoverageDescriptions>",
                b"<wcs:CoverageDescription/>" + b"</wcs:CoverageDescriptions>",
            ),
        ]:
            with self.subTest(body=body[:30]), self.assertRaises(FetchError):
                parse_description(response(body))

    def test_rejects_ambiguous_axes_crs_and_envelope(self):
        for old, new in [
            (b'axisLabels="lat long"', b'axisLabels="long lat"'),
            (b'srsDimension="2"', b'srsDimension="3"'),
            (b'axisLabels="lat long"', b'axisLabels="lat lat"'),
            (b"EPSG/0/4326", b"EPSG/0/3857"),
            (b">50 14<", b">53 14<"),
            (b">50 14<", b">50 14 0<"),
            (b">50 14<", b">NaN 14<"),
            (b">50 14<", b">1e999 14<"),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                parse_description(response(XML.replace(old, new)))

    def test_rejects_invalid_grid_vectors_origin_and_indices(self):
        for old, new in [
            (b'dimension="2"', b'dimension="3"'),
            (b">long lat<", b">lat long<"),
            (b">19 19<", b">-1 19<"),
            (b">0 0<", b">20 0<"),
            (b">0 0.1<", b">0 0<"),
            (b">-0.1 0<", b">0 0.2<"),
            (b">51.95 14.05<", b">99 14.05<"),
            (b">0 0.1<", b">0 0.1 0<"),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                parse_description(response(XML.replace(old, new)))
        extra = XML.replace(
            b"</gml:RectifiedGrid>", b"<gml:offsetVector>0 1</gml:offsetVector></gml:RectifiedGrid>"
        )
        with self.assertRaises(FetchError):
            parse_description(response(extra))

    def test_rejects_changed_or_missing_range_metadata(self):
        for old, new in [
            (b'name="band1"', b'name="band2"'),
            (b">0 255<", b">255 0<"),
            (b'code="W.m-2.Sr-1"', b'code=""'),
            (b'<swe:uom code="W.m-2.Sr-1"/>', b""),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                parse_description(response(XML.replace(old, new)))

    def test_changed_unit_is_preserved_but_never_approved(self):
        result = parse_description(response(XML.replace(b"W.m-2.Sr-1", b"class-code")))
        self.assertEqual(result["range"]["uom_raw"], "class-code")
        self.assertEqual(result["semantic_status"], "unconfirmed")
        self.assertIn("unit_semantics_unconfirmed", result["warnings"])
        self.assertFalse(result["terms_accepted"])

    def test_rejects_unknown_schema_and_range_nil_values(self):
        for old, new in [
            (
                b"<swe:nilValues/>",
                b"<swe:nilValues><swe:NilValues><swe:nilValue>0</swe:nilValue></swe:NilValues></swe:nilValues>",
            ),
            (b"RectifiedGridCoverage", b"ReferenceableGridCoverage"),
            (b"</wcs:CoverageDescriptions>", b"<unexpected/></wcs:CoverageDescriptions>"),
            (b"<gml:domainSet>", b"<gml:domainSet/><gml:domainSet>"),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                parse_description(response(XML.replace(old, new)))

    def test_range_constraints_are_raw_not_a_decoded_legend_or_nodata(self):
        result = parse_description(response())
        self.assertEqual(result["range"]["allowed_interval_raw"], "0 255")
        self.assertEqual(result["range"]["significant_figures_raw"], "3")
        self.assertIsNone(result["nodata"])


class CdiProbeTests(SimpleTestCase):
    def test_fetch_command_returns_metadata_only_and_checks_actual_cli(self):
        output = io.StringIO()
        with patch("ingestion.edo_cdi.Client.get", return_value=response()) as get:
            call_command("edo_cdi_preflight", fetch=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], URL)
        self.assertTrue(result["fetched"])
        self.assertIsNone(result["observation_time"])
        self.assertFalse(result["resources_downloaded"])
        self.assertNotIn("payload", result)
        self.assertNotIn("features", result)
        self.assertIn("checked_at", result)

    def test_command_errors_are_generic_without_partial_output(self):
        output = io.StringIO()
        with patch("ingestion.edo_cdi.Client.get", side_effect=FetchError("private diagnostic")):
            with self.assertRaises(CommandError) as caught:
                call_command("edo_cdi_preflight", fetch=True, stdout=output)
        self.assertNotIn("private diagnostic", str(caught.exception))
        self.assertEqual(output.getvalue(), "")

    def make_transport(self, status=200, robots=b"User-agent: *\nAllow: /", body=XML):
        calls = []

        class FakeHttps(BaseHandler):
            handler_order = 100

            def https_open(self, req):
                calls.append(req.full_url)
                is_robots = req.full_url.endswith("/robots.txt")
                headers = Message()
                headers["Content-Type"] = "text/plain" if is_robots else "text/xml"
                headers["Location"] = URL + "&redirect=1"
                result = addinfourl(
                    io.BytesIO(robots if is_robots else body),
                    headers,
                    req.full_url,
                    200 if is_robots else status,
                )
                result.msg = "Synthetic response"
                return result

        client = Client()
        client.opener.add_handler(FakeHttps())
        return client, calls

    def test_all_redirect_codes_stop_before_destination_get(self):
        for code in [301, 302, 303, 307, 308]:
            client, calls = self.make_transport(status=code)
            with (
                self.subTest(code=code),
                patch("ingestion.edo_cdi.Client", return_value=client),
                patch("ingestion.transport.validate_url"),
                patch("ingestion.transport.time.sleep"),
            ):
                with self.assertRaises(FetchError):
                    preflight(fetch=True)
            self.assertEqual(calls, ["https://drought.emergency.copernicus.eu/robots.txt", URL])

    def test_disallow_stops_before_metadata_request(self):
        client, calls = self.make_transport(robots=b"User-agent: *\nDisallow: /api/wcs")
        with (
            patch("ingestion.edo_cdi.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
        ):
            with self.assertRaises(FetchError):
                preflight(fetch=True)
        self.assertEqual(calls, ["https://drought.emergency.copernicus.eu/robots.txt"])

    def test_bounded_transport_rejects_oversized_response(self):
        client, calls = self.make_transport(body=b" " * (MAX_BYTES + 1))
        with (
            patch("ingestion.edo_cdi.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
        ):
            with self.assertRaises(FetchError):
                preflight(fetch=True)
        self.assertEqual(len(calls), 2)

    def test_real_transport_with_fake_https_has_no_child_requests(self):
        client, calls = self.make_transport()
        with (
            patch("ingestion.edo_cdi.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
        ):
            result = preflight(fetch=True)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["access_policy"], "allowed-or-404")
        self.assertFalse(result["database_writes"])
