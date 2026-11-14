from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from ingestion.discovery_457 import capabilities, discover, interface_links, json_summary
from ingestion.transport import FetchError

WMS = b"""<?xml version="1.0"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
<Service><Title>EFFIS</Title><Fees>none</Fees><AccessConstraints>CC BY 4.0</AccessConstraints>
</Service><Capability><Layer><Title>root</Title>
<Layer><Name>modis.ba</Name><Title>Burnt areas</Title></Layer>
<Layer><Name>ecmwf.fwi</Name><Title>Fire Weather Index</Title></Layer>
</Layer></Capability></WMS_Capabilities>"""

HOME = b"""<html><a href="/api/wms">Map</a><a href="https://example.com/data.csv">x</a>
<a href="/about">About</a><a href="https://drought.emergency.copernicus.eu/download/cdi.zip">
CDI</a><a href="/api/wms#top">dup</a></html>"""


def response(body, url, media="text/html"):
    return SimpleNamespace(
        body=body, url=url, status=200, headers={"content-type": f"{media}; charset=utf-8"}
    )


class DiscoveryParsersTests(SimpleTestCase):
    def test_interface_links_keep_official_hosts_and_dedupe(self):
        links = interface_links("https://drought.emergency.copernicus.eu/", HOME)
        self.assertEqual(
            links,
            [
                "https://drought.emergency.copernicus.eu/api/wms",
                "https://drought.emergency.copernicus.eu/download/cdi.zip",
            ],
        )

    def test_capabilities_lists_named_layers_and_terms(self):
        result = capabilities(WMS)
        self.assertEqual(result["version"], "1.3.0")
        self.assertEqual(result["title"], "EFFIS")
        self.assertEqual(result["access_constraints"], "CC BY 4.0")
        self.assertEqual([layer["name"] for layer in result["layers"]], ["modis.ba", "ecmwf.fwi"])

    def test_capabilities_rejects_entities(self):
        with self.assertRaises(FetchError):
            capabilities(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>')

    def test_json_summary_reports_stac_collections(self):
        result = json_summary(
            b'{"collections":[{"id":"a","license":"other"}],"numberMatched":5,'
            b'"links":[{"rel":"next","href":"x"}]}'
        )
        self.assertEqual(result["ids"], ["a"])
        self.assertEqual(result["number_matched"], 5)
        self.assertTrue(result["next"])


class DiscoverTests(SimpleTestCase):
    def test_bounded_probe_follows_advertised_wms_without_writes(self):
        client = Mock()
        client.get.side_effect = [
            response(HOME, "https://drought.emergency.copernicus.eu/"),
            response(WMS, "https://drought.emergency.copernicus.eu/api/wms", "text/xml"),
        ]
        result = discover({200}, client=client)
        rows = result["registries"][200]
        self.assertEqual([row["kind"] for row in rows], ["html", "ogc"])
        self.assertEqual(rows[1]["capabilities"]["layer_count"], 2)
        self.assertIn("request=GetCapabilities", client.get.call_args_list[1].args[0])
        self.assertFalse(result["database_writes"])
        self.assertEqual(list(result["registries"]), [200])

    def test_blocked_probe_is_reported_not_raised(self):
        client = Mock()
        client.get.side_effect = FetchError("robots.txt disallows this path", 403)
        rows = discover({204}, client=client)["registries"][204]
        self.assertEqual(rows[0]["status"], "blocked")
        self.assertEqual(rows[0]["http"], 403)

    def test_every_effis_probe_runs_once_when_all_fail(self):
        # SimpleTestCase forbids database queries, so a write would fail the test.
        client = Mock()
        client.get.side_effect = FetchError("timeout")
        rows = discover({202}, client=client)["registries"][202]
        self.assertEqual([row["status"] for row in rows], ["blocked", "blocked"])
        self.assertEqual(client.get.call_count, 2)

    def test_wms_link_answering_html_lists_links(self):
        client = Mock()
        client.get.side_effect = [
            response(HOME, "https://drought.emergency.copernicus.eu/"),
            response(HOME, "https://drought.emergency.copernicus.eu/api/wms"),
        ]
        rows = discover({200}, client=client)["registries"][200]
        self.assertEqual(rows[1]["status"], "html_page")
        self.assertIn("https://drought.emergency.copernicus.eu/download/cdi.zip", rows[1]["links"])
