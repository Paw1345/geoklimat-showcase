import json
from unittest.mock import Mock

from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ingestion.management.commands.discover_gios import DATASETS, discover
from ingestion.transport import FetchError, Response


class GiosDiscoveryTests(SimpleTestCase):
    def mock_client(self, institution="100", link=None, incomplete=False):
        client = Mock()

        def get(url):
            if url.endswith("robots.txt"):
                raise FetchError("Cannot confirm robots policy", 200)
            if "/resources?" in url:
                data = {
                    "data": [
                        {
                            "id": "1",
                            "type": "resource",
                            "attributes": {
                                "title": "Official resource",
                                "link": link or "https://dane.gios.gov.pl/api/halas/v1/pomiar",
                            },
                        }
                    ],
                    "meta": {"count": 2 if incomplete else 1},
                    "links": {"next": None},
                }
            else:
                data = {
                    "data": {
                        "id": url.rsplit("/", 1)[1],
                        "type": "dataset",
                        "relationships": {
                            "institution": {"data": {"type": "institution", "id": institution}},
                            "resources": {"meta": {"count": 1}},
                        },
                    }
                }
            return Response(200, json.dumps(data).encode(), {}, url)

        client.get.side_effect = get
        return client

    def test_catalogue_only_reports_block_and_never_fetches_resources(self):
        client = self.mock_client()
        report = discover(client)
        self.assertEqual(len(report["channels"]), len(DATASETS))
        self.assertEqual(report["access"]["status"], "blocked")
        self.assertFalse(report["database_writes"])
        self.assertFalse(report["resources_fetched"])
        self.assertEqual(client.get.call_count, 13)
        self.assertTrue(all("/api/halas" not in call.args[0] for call in client.get.call_args_list))

    def test_wrong_institution_rejected(self):
        with self.assertRaisesMessage(CommandError, "institution"):
            discover(self.mock_client(institution="23"))

    def test_incomplete_catalogue_rejected(self):
        with self.assertRaisesMessage(CommandError, "incomplete"):
            discover(self.mock_client(incomplete=True))

    def test_unreviewed_resource_link_rejected(self):
        for link in (
            "https://example.com/api/halas",
            "http://dane.gios.gov.pl/api/halas",
            "https://dane.gios.gov.pl@evil.example/api/halas",
        ):
            with self.subTest(link=link), self.assertRaises(CommandError):
                discover(self.mock_client(link=link))
