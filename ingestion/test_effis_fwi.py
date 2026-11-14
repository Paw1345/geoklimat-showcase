"""Synthetic WMS metadata, no map or provider requests in tests."""

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

from ingestion.effis_fwi import MAX_BYTES, URL, parse_metadata, preflight
from ingestion.transport import Client, FetchError, Response

XML = b"""<WMS_Capabilities xmlns="http://www.opengis.net/wms"
xmlns:xlink="http://www.w3.org/1999/xlink" version="1.3.0">
<Service><Title>EFFIS</Title><Fees>none</Fees><AccessConstraints>None</AccessConstraints>
<ContactInformation>private contact</ContactInformation></Service><Capability>
<Request><GetMap><Format>image/png</Format><DCPType><HTTP><Get><OnlineResource
xlink:href="http://maps.effis.emergency.copernicus.eu/effis?language=eng&amp;"/>
</Get></HTTP></DCPType></GetMap><GetFeatureInfo><Format>text/plain</Format><DCPType>
<HTTP><Get><OnlineResource
xlink:href="http://maps.effis.emergency.copernicus.eu/effis?language=eng&amp;"/>
</Get></HTTP></DCPType></GetFeatureInfo></Request>
<Layer queryable="1"><CRS>EPSG:4326</CRS><CRS>EPSG:3857</CRS>
<Dimension name="time" units="ISO8601" default="2021-01-01" nearestValue="0">
2018-01-01/2099-12-31</Dimension>
<Layer queryable="0"><Name>mf010.fwi</Name><Title>mf010.fwi</Title>
<Abstract>untrusted prose</Abstract><EX_GeographicBoundingBox>
<westBoundLongitude>-180</westBoundLongitude><eastBoundLongitude>180</eastBoundLongitude>
<southBoundLatitude>-90</southBoundLatitude><northBoundLatitude>90</northBoundLatitude>
</EX_GeographicBoundingBox><Style><Name>default</Name><Title>default</Title>
<LegendURL><Format>image/png</Format><OnlineResource xlink:href="https://example.org/legend"/>
</LegendURL></Style></Layer></Layer></Capability></WMS_Capabilities>"""


def response(body=XML, **kwargs):
    return Response(
        kwargs.get("status", 200),
        body,
        {"content-type": kwargs.get("media", "text/xml; charset=UTF-8")},
        kwargs.get("url", URL),
    )


class FwiMetadataTests(SimpleTestCase):
    def test_raw_range_inherited_time_and_queryable_override_are_preserved(self):
        result = parse_metadata(response())
        self.assertEqual(result["time"]["raw"], "\n2018-01-01/2099-12-31")
        self.assertEqual(result["time"]["default_raw"], "2021-01-01")
        self.assertEqual(result["time"]["units_raw"], "ISO8601")
        self.assertFalse(result["layer"]["queryable"])
        self.assertEqual(result["layer"]["crs"], ["EPSG:3857", "EPSG:4326"])
        self.assertEqual(result["sha256"], hashlib.sha256(XML).hexdigest())
        self.assertFalse(result["product_ready"])
        self.assertIsNone(result["value_unit"])
        self.assertIsNone(result["nodata"])

    def test_whitelist_never_returns_contact_prose_or_child_urls(self):
        rendered = json.dumps(parse_metadata(response()))
        for excluded in ["private contact", "untrusted prose", "example.org", "language=eng"]:
            self.assertNotIn(excluded, rendered)
        self.assertFalse(parse_metadata(response())["terms_accepted"])

    def test_default_cli_has_no_transport_and_no_database_queries(self):
        out = io.StringIO()
        with patch("ingestion.effis_fwi.Client", side_effect=AssertionError("HTTP")):
            call_command("effis_fwi_preflight", stdout=out)
        self.assertFalse(json.loads(out.getvalue())["fetched"])

    def test_response_identity_media_status_and_budget_fail_closed(self):
        for bad in [
            response(status=304),
            response(status=206),
            response(url=URL + "&TIME=x"),
            response(media="text/html"),
            response(b""),
            response(b" " * (MAX_BYTES + 1)),
        ]:
            with self.subTest(status=bad.status), self.assertRaises(FetchError):
                parse_metadata(bad)

    def test_unsafe_or_changed_xml_rejected(self):
        for body in [
            b"\xff",
            XML + b"\x00",
            b"<broken>",
            b'<!DOCTYPE x SYSTEM "https://example.org/xxe">' + XML,
            b'<!DOCTYPE x [<!ENTITY e "huge">]>' + XML,
            XML.replace(b'version="1.3.0"', b'version="1.1.1"'),
            XML.replace(b"http://www.opengis.net/wms", b"http://wrong.example/wms"),
        ]:
            with self.subTest(body=body[:25]), self.assertRaises(FetchError):
                parse_metadata(response(body))

    def test_missing_duplicate_and_malformed_layer_identity_fail(self):
        for body in [
            XML.replace(b"mf010.fwi", b"mf010.isi"),
            XML.replace(b"<Name>mf010.fwi</Name>", b"<Name>mf010.fwi</Name>" * 2),
            XML.replace(b"<Title>mf010.fwi</Title>", b"<Title>changed</Title>"),
            XML.replace(b'<Layer queryable="0">', b'<Layer queryable="yes">'),
            XML.replace(b"<CRS>EPSG:4326</CRS>", b"<CRS>private</CRS>"),
            XML.replace(
                b'<Layer queryable="0">',
                b'<Layer queryable="0"><Dimension '
                b'name="time" units="ISO8601" default="2021-01-01">x</Dimension>' * 2,
            ),
        ]:
            with self.subTest(body=body[-40:]), self.assertRaises(FetchError):
                parse_metadata(response(body))

    def test_duplicate_selected_layer_and_tree_budgets_rejected(self):
        start = XML.index(b'<Layer queryable="0">')
        end = XML.index(b"</Layer>", start) + len(b"</Layer>")
        duplicate = XML[:end] + XML[start:end] + XML[end:]
        for body in [
            duplicate,
            XML.replace(b"</Capability>", b"<Layer/>" * 201 + b"</Capability>"),
            XML.replace(b"</Service>", b"<x/>" * 10001 + b"</Service>"),
        ]:
            with self.subTest(size=len(body)), self.assertRaises(FetchError):
                parse_metadata(response(body))

    def test_layer_budget_is_enforced_with_many_valid_siblings(self):
        body = XML.replace(b"</Layer></Layer>", b"</Layer>" + b"<Layer/>" * 201 + b"</Layer>")
        with self.assertRaises(FetchError):
            parse_metadata(response(body))

    def test_inherited_queryable_and_time_can_be_overridden(self):
        body = XML.replace(b'<Layer queryable="0">', b"<Layer>")
        result = parse_metadata(response(body))
        self.assertTrue(result["layer"]["queryable"])
        self.assertNotIn("layer_not_queryable", result["warnings"])
        self.assertFalse(result["product_ready"])
        body = XML.replace(
            b'<Layer queryable="0">',
            b'<Layer queryable="0"><Dimension name="time" units="ISO8601" '
            b'default="2020-01-01">2020-01-01/2020-12-31/P1D</Dimension>',
        )
        self.assertEqual(parse_metadata(response(body))["time"]["raw"], "2020-01-01/2020-12-31/P1D")

    def test_explicit_selected_time_duplicate_missing_and_oversize_fail(self):
        for old, new in [
            (b'name="time"', b'name="height"'),
            (b'nearestValue="0"', b'nearestValue="yes"'),
            (b'default="2021-01-01"', b'default=""'),
            (b"2018-01-01/2099-12-31", b"x" * 2001),
        ]:
            with self.subTest(new=new[:25]), self.assertRaises(FetchError):
                parse_metadata(response(XML.replace(old, new)))
        dimension = XML[XML.index(b"<Dimension") : XML.index(b"</Dimension>") + 12]
        with self.assertRaises(FetchError):
            parse_metadata(response(XML.replace(dimension, dimension * 2)))

    def test_geographic_extent_not_valid_without_finite_ordered_coordinates(self):
        for old, new in [
            (b">-180<", b">NaN<"),
            (b">180<", b">181<"),
            (b">-90<", b">91<"),
            (b">90<", b">-90<"),
            (b">180<", b">1e999<"),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                parse_metadata(response(XML.replace(old, new)))

    def test_operation_formats_and_endpoints_have_bounded_reviewed_shape(self):
        for old, new in [
            (b"<Format>text/plain</Format>", b""),
            (b"<Format>text/plain</Format>", b"<Format>text/plain</Format>" * 2),
            (b"<Format>text/plain</Format>", b"<Format>" + b"x" * 101 + b"</Format>"),
            (
                b"maps.effis.emergency.copernicus.eu/effis?language=eng",
                b"attacker.example/effis?secret=private",
            ),
        ]:
            with self.subTest(new=new[:30]), self.assertRaises(FetchError):
                parse_metadata(response(XML.replace(old, new)))
        result = parse_metadata(response())
        self.assertEqual(result["operations"]["GetFeatureInfo"]["formats"], ["text/plain"])
        self.assertIn("advertised_operation_http_not_requested", result["warnings"])

    def test_changed_time_units_stay_raw_and_never_become_value_units(self):
        result = parse_metadata(response(XML.replace(b' units="ISO8601"', b' units="changed"')))
        self.assertEqual(result["time"]["units_raw"], "changed")
        self.assertIsNone(result["value_unit"])
        self.assertFalse(result["time"]["availability_verified"])
        self.assertFalse(result["full_dataset_coverage"])

    def test_reviewed_lowercase_crs_codes_use_canonical_uppercase(self):
        result = parse_metadata(response(XML.replace(b"EPSG:4326", b"epsg:4326")))
        self.assertEqual(result["layer"]["crs"], ["EPSG:3857", "EPSG:4326"])

    def test_foreign_namespace_crs_cannot_bypass_canonical_helper_validation(self):
        body = XML.replace(
            b"<CRS>EPSG:4326</CRS>", b'<CRS xmlns="http://wrong.example/wms">EPSG:4326</CRS>'
        )
        with self.assertRaises(FetchError):
            parse_metadata(response(body))


class FwiProbeTests(SimpleTestCase):
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

    def test_all_redirect_codes_stop_before_destination_request(self):
        for status in (301, 302, 303, 307, 308):
            client, calls = self.make_transport(status=status)
            with (
                self.subTest(status=status),
                patch("ingestion.effis_fwi.Client", return_value=client),
                patch("ingestion.transport.validate_url"),
                patch("ingestion.transport.time.sleep"),
                self.assertRaises(FetchError),
            ):
                preflight(fetch=True)
            self.assertEqual(calls, ["https://maps.effis.emergency.copernicus.eu/robots.txt", URL])

    def test_disallow_stops_before_capabilities(self):
        client, calls = self.make_transport(robots=b"User-agent: *\nDisallow: /effis")
        with (
            patch("ingestion.effis_fwi.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
            self.assertRaises(FetchError),
        ):
            preflight(fetch=True)
        self.assertEqual(calls, ["https://maps.effis.emergency.copernicus.eu/robots.txt"])

    def test_transport_rejects_oversize_and_never_follows_child_operations(self):
        client, calls = self.make_transport(body=b" " * (MAX_BYTES + 1))
        with (
            patch("ingestion.effis_fwi.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
            self.assertRaises(FetchError),
        ):
            preflight(fetch=True)
        self.assertEqual(len(calls), 2)

    def test_real_transport_fake_https_only_fetches_robots_and_metadata(self):
        client, calls = self.make_transport()
        with (
            patch("ingestion.effis_fwi.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
        ):
            result = preflight(fetch=True)
        self.assertEqual(calls, ["https://maps.effis.emergency.copernicus.eu/robots.txt", URL])
        self.assertTrue(result["fetched"])
        self.assertFalse(result["resources_downloaded"])
        self.assertEqual(result["access_policy"], "allowed-or-404")

    def test_fetch_cli_outputs_whitelisted_metadata_without_database(self):
        out = io.StringIO()
        with patch("ingestion.effis_fwi.Client.get", return_value=response()) as get:
            call_command("effis_fwi_preflight", fetch=True, stdout=out)
        self.assertEqual(get.call_args.args[0], URL)
        self.assertEqual(get.call_count, 1)
        report = json.loads(out.getvalue())
        self.assertIn("checked_at", report)
        self.assertFalse(report["database_writes"])
        self.assertFalse(report["product_ready"])

    def test_cli_error_never_leaks_private_diagnostic_or_partial_json(self):
        out = io.StringIO()
        with patch("ingestion.effis_fwi.Client.get", side_effect=FetchError("private diagnostic")):
            with self.assertRaises(CommandError) as caught:
                call_command("effis_fwi_preflight", fetch=True, stdout=out)
        self.assertNotIn("private diagnostic", str(caught.exception))
        self.assertEqual(out.getvalue(), "")
