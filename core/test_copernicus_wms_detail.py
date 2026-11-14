from django.test import TestCase

from ingestion.copernicus_wms import PROFILES, config
from ingestion.models import Item, Source


class CopernicusWmsDetailTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="wms-detail",
            name=PROFILES["edo"]["name"],
            organization=PROFILES["edo"]["organization"],
            adapter="ogc",
            url=PROFILES["edo"]["url"],
            config=config("edo"),
        )

    def detail(self, **metadata):
        item = Item.objects.create(
            source=self.source,
            external_id="layer",
            title="Combined Drought Indicator",
            url=self.source.url,
            metadata={
                "record_kind": "service_capabilities",
                "service": "WMS",
                "version": "1.1.1",
                "terms_url": PROFILES["edo"]["terms"],
                **metadata,
            },
        )
        response = self.client.get(f"/items/{item.pk}/")
        self.assertEqual(response.status_code, 200)
        return response

    def test_layer_time_attribution_and_terms_visible_without_fetching_map(self):
        response = self.detail(
            layer_name="cdiad",
            time_first="2024-07-01",
            time_last="2026-09-21",
            time_default="2026-09-21",
            preview_url="https://drought.emergency.copernicus.eu/api/wms?REQUEST=GetMap",
        )
        self.assertContains(response, PROFILES["edo"]["organization"])
        self.assertContains(response, "2024-07-01 / 2026-09-21")
        self.assertContains(response, "Warunki wykorzystania")
        self.assertContains(response, "terms&amp;conditions")
        self.assertContains(response, "Otwórz mapę dostawcy dla obszaru Polski")
        self.assertNotContains(response, "<img")

    def test_missing_time_and_crs_and_untrusted_layer_name_remain_explicit(self):
        response = self.detail(layer_name="<script>alert(1)</script>")
        self.assertContains(response, "Nie podano / Nie podano")
        self.assertContains(response, "warstwa nie deklaruje EPSG:4326")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_service_group_count_is_not_presented_as_named_layers(self):
        response = self.detail(layers_count=31)
        self.assertContains(response, "31 (obejmuje grupy)")
        self.assertNotContains(response, "Nazwa warstwy w usłudze")
