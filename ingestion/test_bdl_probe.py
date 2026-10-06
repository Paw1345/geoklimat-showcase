import io
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ingestion.bdl_probe import LICENSE, OPENAPI, plan, probe
from ingestion.transport import FetchError, Response


class BdlProbeTests(SimpleTestCase):
    def setUp(self):
        self.url = plan("rdlp")["url"]
        self.api = {
            "info": {
                "title": "Usługa OGC API Features Banku Danych o Lasach",
                "contact": {"name": "Bank Danych o Lasach"},
                "license": {"url": LICENSE},
                "termsOfService": LICENSE,
            }
        }
        self.data = {
            "type": "FeatureCollection",
            "numberMatched": 17,
            "numberReturned": 1,
            "links": [
                {"rel": "self", "href": self.url},
                {"rel": "next", "href": self.url.split("?")[0] + "?offset=1&limit=1"},
            ],
            "features": [
                {
                    "type": "Feature",
                    "id": 1,
                    "properties": {"a_year": 2025},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[14, 50], [15, 50], [15, 51], [14, 50]]],
                    },
                }
            ],
        }
        self.client = Mock(max_bytes=5 * 1024 * 1024)
        self.respond()

    def respond(self):
        self.client.get.side_effect = [
            Response(200, json.dumps(self.api).encode(), {}, OPENAPI),
            Response(200, json.dumps(self.data).encode(), {}, self.url),
        ]

    def test_two_gets_no_child_pagination_or_payload_output(self):
        result = probe("rdlp", self.client)
        self.assertEqual(self.client.get.call_count, 2)
        self.assertTrue(result["next_page_validated_not_fetched"])
        self.assertEqual(result["source_years"], [2025])
        self.assertFalse(result["full_dataset_coverage"])
        self.assertFalse(result["database_writes"])
        self.assertFalse(result["portal_terms_resolved_for_bulk_reuse"])
        self.assertNotIn("features", result)
        self.assertEqual(len(result["response_sha256"]), 64)

    def test_plan_default_command_does_not_touch_http_or_database(self):
        with patch("ingestion.transport.Client.get") as get:
            output = io.StringIO()
            call_command("bdl_probe", stdout=output)
            self.assertFalse(json.loads(output.getvalue())["fetched"])
            get.assert_not_called()

    def test_profile_limits(self):
        for collection, offset in (("unknown", 0), ("rdlp", -1), ("rdlp", True), ("rdlp", 1000001)):
            with self.assertRaises(FetchError):
                plan(collection, offset)

    def test_only_zero_offset_may_be_omitted_from_canonical_self(self):
        self.data["links"][0]["href"] = self.url.removesuffix("&offset=0")
        self.respond()
        self.assertTrue(probe("rdlp", self.client)["fetched"])
        self.url = plan("rdlp", 1)["url"]
        self.data["links"][0]["href"] = self.url.removesuffix("&offset=1")
        self.respond()
        with self.assertRaises(FetchError):
            probe("rdlp", self.client, 1)

    def test_changed_license_stops_before_geometry_get(self):
        self.api["info"]["license"]["url"] = "https://example.com/terms"
        self.respond()
        with self.assertRaises(FetchError):
            probe("rdlp", self.client)
        self.assertEqual(self.client.get.call_count, 1)

    def test_schema_and_geometry_fail_closed(self):
        original = deepcopy(self.data)
        mutations = (
            lambda d: d.update(numberReturned=True),
            lambda d: d.update(numberMatched=0),
            lambda d: d.update(features=[]),
            lambda d: d["features"][0].update(id=True),
            lambda d: d["features"][0]["properties"].update(a_year="2025"),
            lambda d: d["features"][0].update(geometry=None),
            lambda d: d["features"][0]["geometry"]["coordinates"][0].append([14, 51]),
            lambda d: d["features"][0]["geometry"]["coordinates"][0][0].__setitem__(0, 181),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.data = deepcopy(original)
                mutation(self.data)
                self.respond()
                with self.assertRaises(FetchError):
                    probe("rdlp", self.client)

    def test_hostile_pagination_is_not_followed(self):
        for url in (
            "https://evil.invalid/collections/rdlp/items?offset=1&limit=1",
            self.url.split("?")[0] + "?offset=2&limit=1",
            self.url.split("?")[0] + "?offset=1&limit=1&f=csv",
            self.url.split("?")[0] + "?offset=1&limit=1&limit=2",
        ):
            self.data["links"][1]["href"] = url
            self.respond()
            with self.assertRaises(FetchError):
                probe("rdlp", self.client)

    def test_duplicate_json_and_size_budget_rejected(self):
        for body in (b'{"info":{},"info":{}}', b"x" * (5 * 1024 * 1024 + 1)):
            self.client.get.side_effect = [Response(200, body, {}, OPENAPI)]
            with self.assertRaises(FetchError):
                probe("rdlp", self.client)

    def test_command_error_does_not_leak_payload(self):
        with patch(
            "ingestion.management.commands.bdl_probe.probe",
            side_effect=FetchError("private payload"),
        ):
            with self.assertRaisesMessage(CommandError, "BDL probe failed") as caught:
                call_command("bdl_probe", fetch=True, stdout=io.StringIO())
            self.assertNotIn("private payload", str(caught.exception))
