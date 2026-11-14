import io
import json
from unittest.mock import Mock

from django.core.management import call_command
from django.test import SimpleTestCase

from ingestion.gdos_preflight import TARGETS, capabilities_summary, preflight
from ingestion.transport import FetchError, Response

WMS = (
    b'<?xml version="1.0"?><WMS_Capabilities xmlns="http://www.opengis.net/wms">'
    b"<Service><Title>GDOS SDI</Title><Fees>brak</Fees>"
    b"<AccessConstraints>none</AccessConstraints></Service><Capability><Layer>"
    b"<Title>root</Title><Layer><Name>ParkiNarodowe</Name></Layer>"
    b"<Layer><Name>Rezerwaty</Name></Layer></Layer></Capability></WMS_Capabilities>"
)


class GdosPreflightTests(SimpleTestCase):
    def test_default_is_plan_without_io(self):
        out = io.StringIO()
        call_command("gdos_sdi_preflight", stdout=out)
        data = json.loads(out.getvalue())
        self.assertEqual(data["writes"], "none")
        self.assertEqual(set(data["targets"]), {"sdi-wms", "sdi-wfs", "geoserwis"})
        hosts = {u.split("/")[2] for u in TARGETS.values()}
        self.assertEqual(hosts, {"sdi.gdos.gov.pl", "geoserwis.gdos.gov.pl"})

    def test_fetch_reports_robots_block_and_capabilities(self):
        client = Mock()
        client._request.side_effect = lambda url: Response(
            200, b"<html><body>x</body></html>", {"content-type": "text/html"}, url
        )

        def get(url):
            if "geoserwis" in url:
                raise FetchError("Cannot confirm robots policy", 200)
            return Response(200, WMS, {}, url)

        client.get.side_effect = get
        result = preflight(fetch=True, client=client)["targets"]
        self.assertEqual(result["geoserwis"]["error"], "Cannot confirm robots policy")
        self.assertTrue(result["geoserwis"]["robots"]["html"])
        caps = result["sdi-wms"]["capabilities"]
        self.assertEqual((caps["named_layers"], caps["Fees"]), (2, "brak"))
        self.assertEqual(client.get.call_count, 3)

    def test_dtd_rejected(self):
        with self.assertRaises(FetchError):
            capabilities_summary(b'<!DOCTYPE x [<!ENTITY a "b">]><WMS_Capabilities/>')
