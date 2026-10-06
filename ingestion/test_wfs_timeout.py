from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from ingestion.transport import Client, request_timeout


class WfsTimeoutTests(SimpleTestCase):
    url = (
        "https://mapy.geoportal.gov.pl/wss/service/wfsGN/guest?"
        "SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature&"
        "TYPENAMES=gn%3ANamedPlace&STARTINDEX=180500"
    )

    def test_timeout_is_pinned_to_reviewed_paged_service(self):
        self.assertEqual(request_timeout(self.url), 60)
        for url in (
            self.url.replace("mapy.geoportal.gov.pl", "other.example"),
            self.url.replace("https:", "http:"),
            self.url.replace("/wfsGN/", "/other/"),
            self.url.replace("GetFeature", "GetCapabilities"),
            self.url.replace("gn%3ANamedPlace", "other"),
            self.url.replace("2.0.0", "1.0.0"),
            self.url.replace("&STARTINDEX=180500", ""),
            self.url + "&STARTINDEX=0",
            self.url.replace("180500", "-1"),
            "https://mapy.geoportal.gov.pl/robots.txt",
        ):
            with self.subTest(url=url):
                self.assertEqual(request_timeout(url), 15)

    @patch("ingestion.transport.validate_url")
    def test_raw_uses_budget_without_changing_body_limit(self, validate):
        client = Client()
        reply = MagicMock()
        reply.__enter__.return_value = reply
        reply.code = 200
        reply.headers = {}
        reply.read.side_effect = [b"{}", b""]
        client.opener.open = MagicMock(return_value=reply)
        result = client._raw(self.url, delay=0)
        self.assertEqual(result.body, b"{}")
        self.assertEqual(client.opener.open.call_args.kwargs["timeout"], 60)
        self.assertEqual(client.max_bytes, 5 * 1024 * 1024)
        validate.assert_called_once_with(self.url)


class StacTimeoutTests(SimpleTestCase):
    url = "https://stac.dataspace.copernicus.eu/v1/search?bbox=1,2,3,4"

    def test_timeout_is_pinned_to_copernicus_search(self):
        self.assertEqual(request_timeout(self.url), 30)
        for url in (
            self.url.replace("stac.dataspace.copernicus.eu", "other.example"),
            self.url.replace("https:", "http:"),
            self.url.replace("/v1/search", "/v1/collections"),
            "https://stac.dataspace.copernicus.eu/robots.txt",
        ):
            with self.subTest(url=url):
                self.assertEqual(request_timeout(url), 15)
