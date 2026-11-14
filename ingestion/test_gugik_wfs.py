import io
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.gugik_wfs import SERVICES, config, slug, url
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response

WFS2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0"
 xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
<ows:ServiceIdentification><ows:Title>Strefy turbin</ows:Title>
<ows:Abstract>Opis</ows:Abstract><ows:Fees>brak</ows:Fees>
<ows:AccessConstraints>Warunki GUGiK</ows:AccessConstraints></ows:ServiceIdentification>
<wfs:FeatureTypeList>
<wfs:FeatureType><wfs:Name>ms:Strefy</wfs:Name><wfs:Title>Strefy</wfs:Title>
<wfs:DefaultCRS>urn:ogc:def:crs:EPSG::2180</wfs:DefaultCRS></wfs:FeatureType>
<wfs:FeatureType><wfs:Name>ms:Maszty</wfs:Name><wfs:Title>Maszty</wfs:Title></wfs:FeatureType>
</wfs:FeatureTypeList></wfs:WFS_Capabilities>"""
WFS11 = b"""<WFS_Capabilities xmlns="http://www.opengis.net/wfs" version="1.1.0"
 xmlns:ows="http://www.opengis.net/ows"><ows:ServiceIdentification><ows:Title>AU</ows:Title>
</ows:ServiceIdentification><FeatureTypeList><FeatureType><Name>au:Unit</Name>
<Title>Unit</Title><DefaultSRS>EPSG:4326</DefaultSRS></FeatureType></FeatureTypeList>
</WFS_Capabilities>"""


class GugikWfsTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=slug("turbiny-strefy"),
            name="GUGiK",
            adapter="ogc",
            url=url("turbiny-strefy"),
            config=config("turbiny-strefy"),
        )

    def fetch(self, body, source=None):
        source = source or self.source
        http = Mock()
        http.get.return_value = Response(200, body, {}, source.url)
        return fetch_entries(source, http)

    def test_service_and_each_feature_type_become_entries(self):
        entries, bad, response, _ = self.fetch(WFS2)
        self.assertEqual((len(entries), bad, response.status), (3, 0, 200))
        self.assertEqual(entries[0].title, "Strefy turbin")
        self.assertEqual(entries[0].metadata["feature_types_count"], 2)
        self.assertEqual(entries[1].metadata["feature_type"], "ms:Strefy")
        self.assertEqual(entries[1].metadata["default_crs"], "urn:ogc:def:crs:EPSG::2180")
        for entry in entries:
            self.assertFalse(entry.metadata["features_downloaded"])
            self.assertEqual(entry.metadata["access_constraints_raw"], "Warunki GUGiK")
            self.assertEqual(entry.metadata["version"], "2.0.0")

    def test_wfs_110_namespace_accepted(self):
        entries, _, _, _ = self.fetch(WFS11)
        self.assertEqual(entries[1].metadata["default_crs"], "EPSG:4326")

    def test_rejects_entities_other_xml_and_repeats(self):
        bad_bodies = [
            b'<!DOCTYPE x [<!ENTITY a "b">]>' + WFS2,
            b'<WMS_Capabilities xmlns="http://www.opengis.net/wms"/>',
            WFS2.replace(b"ms:Maszty", b"ms:Strefy"),
            WFS2.replace(b"<wfs:FeatureTypeList>", b"<wfs:FeatureTypeList/><wfs:X>"),
        ]
        for body in bad_bodies:
            with self.subTest(body=body[:30]), self.assertRaises(FetchError):
                self.fetch(body)

    def test_modified_url_rejected(self):
        self.source.url += "&VERSION=1.0.0"
        with self.assertRaises(FetchError):
            self.fetch(WFS2)

    def test_catalogue_urls_stay_on_reviewed_host(self):
        self.assertEqual(len(SERVICES), 44)
        for key in SERVICES:
            self.assertTrue(url(key).startswith("https://mapy.geoportal.gov.pl/wss/"))
            self.assertNotIn("wfsGN", url(key))

    def test_configure_dry_apply_repeat_and_guard(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="f" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release, registry_id=53, master={}, technical={}, integration_state="configured"
        )
        out = io.StringIO()
        call_command("configure_gugik_wfs", stdout=out)
        self.assertIn("Dry-run", out.getvalue())
        self.assertFalse(Source.objects.exists())
        call_command("configure_gugik_wfs", "--apply", stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 44)
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        self.assertFalse(Source.objects.filter(activation_approved=True).exists())
        self.assertEqual(entry.sources.count(), 44)
        entry.refresh_from_db()
        self.assertEqual(entry.integration_state, "configured")
        out = io.StringIO()
        call_command("configure_gugik_wfs", "--apply", stdout=out)
        self.assertEqual(out.getvalue().count("unchanged"), 44)
        Source.objects.filter(slug=slug("prng")).update(url=url("prng") + "&x=1")
        with self.assertRaises(CommandError):
            call_command("configure_gugik_wfs", "--apply", stdout=io.StringIO())
