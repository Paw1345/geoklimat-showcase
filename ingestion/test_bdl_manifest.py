import io
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ingestion.bdl_manifest import MAX_BYTES, URL, fetch_manifest
from ingestion.bdl_probe import LICENSE, OPENAPI
from ingestion.transport import FetchError, Response


class BdlManifestTests(SimpleTestCase):
    def setUp(self):
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
            "numberMatched": 2,
            "numberReturned": 2,
            "links": [{"rel": "self", "href": URL}],
            "features": [self.feature(2), self.feature(1)],
        }
        self.client = Mock(max_bytes=5 * 1024 * 1024)

    def feature(self, identity):
        return {
            "type": "Feature",
            "id": identity,
            "geometry": None,
            "properties": {
                "id": identity,
                "a_year": 2026,
                "region_cd": str(identity),
                "region_name": "RDLP",
            },
        }

    def respond(self):
        self.client.get.side_effect = [
            Response(200, json.dumps(self.api).encode(), {}, OPENAPI),
            Response(200, json.dumps(self.data).encode(), {}, URL),
        ]

    def test_complete_sorted_manifest_without_geometry_or_database(self):
        self.respond()
        result = fetch_manifest(self.client)
        self.assertEqual([item["id"] for item in result["identity_manifest"]], [1, 2])
        self.assertEqual(self.client.max_bytes, MAX_BYTES)
        self.assertEqual(self.client.get.call_count, 2)
        self.assertFalse(result["database_writes"])
        self.assertFalse(result["full_dataset_coverage"])
        self.assertFalse(result["provider_snapshot_consistency_verified"])
        self.assertNotIn("features", result)
        original_hash = result["scope_sha256"]
        self.data["features"].reverse()
        self.respond()
        self.assertEqual(fetch_manifest(self.client)["scope_sha256"], original_hash)
        self.data["features"][0]["properties"]["a_year"] = 2025
        self.respond()
        self.assertNotEqual(fetch_manifest(self.client)["scope_sha256"], original_hash)

    def test_default_plan_no_http(self):
        with patch("ingestion.transport.Client.get") as get:
            output = io.StringIO()
            call_command("bdl_manifest", stdout=output)
            self.assertFalse(json.loads(output.getvalue())["fetched"])
            get.assert_not_called()

    def test_provider_may_omit_redundant_property_id(self):
        for feature in self.data["features"]:
            del feature["properties"]["id"]
        self.respond()
        self.assertEqual(fetch_manifest(self.client)["number_returned"], 2)

    def test_malformed_or_partial_manifest_fail_closed(self):
        original = deepcopy(self.data)
        mutations = (
            lambda d: d.update(numberMatched=True),
            lambda d: d.update(numberMatched=0),
            lambda d: d.update(numberMatched=101),
            lambda d: d.update(numberReturned=True),
            lambda d: d.update(numberReturned=1),
            lambda d: d["features"].pop(),
            lambda d: d["features"].__setitem__(1, self.feature(2)),
            lambda d: d["features"][0].update(id=True),
            lambda d: d["features"][0].update(geometry={}),
            lambda d: d["features"][0]["properties"].update(id=3),
            lambda d: d["features"][0]["properties"].update(a_year="2026"),
            lambda d: d["features"][0]["properties"].update(region_name=""),
            lambda d: d["links"][0].update(href="https://evil.invalid/"),
            lambda d: d["links"].append({"rel": "next", "href": URL}),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.data = deepcopy(original)
                mutation(self.data)
                self.respond()
                with self.assertRaises(FetchError):
                    fetch_manifest(self.client)

    def test_oversize_is_rejected_even_if_client_does_not_enforce_limit(self):
        self.data["padding"] = "x" * MAX_BYTES
        self.respond()
        with self.assertRaises(FetchError):
            fetch_manifest(self.client)

    def test_license_change_stops_before_manifest(self):
        self.api["info"]["license"]["url"] = "https://example.invalid/"
        self.respond()
        with self.assertRaises(FetchError):
            fetch_manifest(self.client)
        self.assertEqual(self.client.get.call_count, 1)

    def test_command_does_not_emit_partial_success_on_failure(self):
        with patch(
            "ingestion.management.commands.bdl_manifest.fetch_manifest",
            side_effect=FetchError("invalid"),
        ):
            output = io.StringIO()
            with self.assertRaises(CommandError):
                call_command("bdl_manifest", fetch=True, stdout=output)
            self.assertEqual(output.getvalue(), "")
