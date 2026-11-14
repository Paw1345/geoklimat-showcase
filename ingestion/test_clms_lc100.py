import io
import json
from copy import deepcopy
from email.message import Message
from unittest.mock import patch
from urllib.request import BaseHandler
from urllib.response import addinfourl

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from ingestion.clms_catalogue import POLICY
from ingestion.clms_lc100 import (
    COLLECTION_ID,
    COLLECTION_URL,
    ITEMS_URL,
    MAX_BYTES,
    SEARCH_URL,
    parse_metadata,
    preflight,
)
from ingestion.transport import Client, FetchError, Response


def collection():
    return {
        "id": COLLECTION_ID,
        "type": "Collection",
        "stac_version": "1.1.0",
        "title": "Synthetic CLMS LC100 collection",
        "license": "other",
        "keywords": ["CLMS"],
        "extent": {
            "spatial": {"bbox": [[-180, -60, 180, 80]]},
            "temporal": {"interval": [["2014-01-01T00:00:00Z", "2020-03-31T23:59:59Z"]]},
        },
        "summaries": {
            "gsd": [100],
            "proj:code": ["EPSG:4326"],
            "processing:version": ["v3.1.2"],
            "product:type": ["dynamic_land_cover"],
        },
        "links": [
            {"rel": "self", "href": COLLECTION_URL},
            {"rel": "license", "href": POLICY},
            {"rel": "items", "href": ITEMS_URL},
        ],
    }


def items():
    return {
        "type": "FeatureCollection",
        "numberReturned": 1,
        "links": [{"rel": "self", "href": ITEMS_URL}],
        "features": [
            {
                "id": "PROBAV_LC100_global_v3.1.2_2019_cog",
                "type": "Feature",
                "collection": COLLECTION_ID,
                "stac_version": "1.1.0",
                "bbox": [-180, -60, 180, 80],
                "geometry": {"type": "Polygon", "coordinates": "ignored"},
                "properties": {
                    "datetime": "2019-01-01T00:00:00.000000Z",
                    "start_datetime": "2019-01-01T00:00:00.000000Z",
                    "end_datetime": "2019-12-31T23:59:59.999999Z",
                    "created": "2025-01-01T12:00:00+01:00",
                    "updated": "2025-01-02T00:00:00Z",
                    "gsd": 100,
                    "proj:code": "EPSG:4326",
                    "processing:version": "v3.1.2",
                    "product:type": "dynamic_land_cover",
                    "_private": "DO_NOT_EXPOSE",
                    "auth:schemes": {"secret": "DO_NOT_EXPOSE"},
                },
                "assets": {
                    "Product": {
                        "type": "application/zip",
                        "roles": ["data", "metadata", "archive"],
                        "file:size": 45_176_261_351,
                        "href": "https://example.org/DO_NOT_EXPOSE",
                    },
                    "discrete_classification_map": {
                        "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                        "roles": ["data"],
                        "file:size": 1_929_967_346,
                        "proj:code": "EPSG:4326",
                        "href": "s3://DO_NOT_EXPOSE/asset",
                    },
                },
            }
        ],
    }


def response(data, url, **kwargs):
    return Response(
        kwargs.pop("status", 200),
        json.dumps(data).encode(),
        kwargs.pop("headers", {"content-type": "application/json"}),
        url,
        **kwargs,
    )


def parsed(catalogue=None, result=None):
    return parse_metadata(
        response(collection() if catalogue is None else catalogue, COLLECTION_URL),
        response(items() if result is None else result, SEARCH_URL),
    )


def changed(data, path, value):
    data = deepcopy(data)
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return data


class Lc100Tests(SimpleTestCase):
    def test_default_cli_has_no_network_or_database_access(self):
        output = io.StringIO()
        with patch("ingestion.clms_lc100.Client") as client:
            call_command("clms_lc100_preflight", stdout=output)
        client.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["fetched"])
        self.assertFalse(plan["database_writes"])
        self.assertEqual(plan["request_urls"], [COLLECTION_URL, SEARCH_URL])

    def test_raw_metadata_and_warnings_without_assets_or_private_fields(self):
        result = parsed()
        self.assertEqual(
            result["product"]["properties_raw"],
            {
                k: v
                for k, v in items()["features"][0]["properties"].items()
                if k not in {"_private", "auth:schemes"}
            },
        )
        self.assertEqual(result["result_state"], "found")
        self.assertFalse(result["resources_downloaded"])
        self.assertFalse(result["exact_geographic_mask_verified"])
        self.assertFalse(result["terms_accepted"])
        self.assertIn("global_product", result["warnings"])
        self.assertIn("asset_exceeds_manual_limit", result["warnings"])
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(result))
        self.assertEqual(result["collection"]["license_raw"], "other")

    def test_empty_is_not_full_coverage_or_error(self):
        data = items()
        data.update(features=[], numberReturned=0)
        result = parsed(result=data)
        self.assertEqual(result["result_state"], "no_results")
        self.assertIsNone(result["product"])
        self.assertFalse(result["full_dataset_coverage"])

    def test_collection_profile_changes_require_review(self):
        for path, value in [
            (("id",), "clms_other"),
            (("license",), "proprietary"),
            (("summaries", "proj:code"), ["EPSG:3035"]),
            (("summaries", "gsd"), [10]),
            (("summaries", "processing:version"), ["v4"]),
            (("summaries", "product:type"), ["snow"]),
            (("links", 2, "href"), "https://example.org/items"),
            (("links", 2, "method"), "POST"),
            (("links", 0, "method"), "POST"),
        ]:
            with self.subTest(path=path, value=value), self.assertRaises(FetchError):
                parsed(catalogue=changed(collection(), path, value))

    def test_root_cardinality_and_paging_guards(self):
        for path, value in [
            (("type",), "Collection"),
            (("numberReturned",), True),
            (("numberReturned",), 0),
            (("features",), items()["features"] * 2),
            (("links",), []),
            (("links", 0, "href"), SEARCH_URL),
            (("links", 0, "method"), "POST"),
            (("links",), items()["links"] + [{"rel": "next", "href": SEARCH_URL}]),
            (("links",), items()["links"] * 2),
        ]:
            with self.subTest(path=path), self.assertRaises(FetchError):
                parsed(result=changed(items(), path, value))

    def test_changed_feature_identity_or_schema_is_not_a_match(self):
        for key, value in [
            ("id", "PROBAV_LC100_global_v3.1.2_2018_cog"),
            ("collection", "clms_other"),
            ("type", "Collection"),
            ("stac_version", "1.0.0"),
            ("properties", None),
            ("assets", []),
            ("bbox", [0, 85, 1, 86]),
            ("bbox", [-180, 80, 180, -60]),
            ("bbox", [False, -60, 180, 80]),
            ("bbox", [-180, -60, 181, 80]),
            ("bbox", [0, 0, 0]),
            ("geometry", None),
            ("license", "proprietary"),
        ]:
            with self.subTest(key=key), self.assertRaises(FetchError):
                parsed(result=changed(items(), ("features", 0, key), value))

    def test_dates_projection_and_version_are_validated_not_inferred_from_id(self):
        for key, value in [
            ("datetime", "2025-01-01T00:00:00Z"),
            ("start_datetime", "2018-01-01T00:00:00Z"),
            ("end_datetime", "2019-12-31T23:59:59Z"),
            ("created", None),
            ("created", "2025-13-01T00:00:00Z"),
            ("updated", "2024-01-01T00:00:00Z"),
            ("updated", "2025-01-02T00:00:00"),
            ("gsd", 10),
            ("proj:code", "EPSG:3035"),
            ("processing:version", "v4"),
            ("product:type", "snow"),
        ]:
            with self.subTest(key=key), self.assertRaises(FetchError):
                parsed(result=changed(items(), ("features", 0, "properties", key), value))

    def test_asset_metadata_has_bounded_types_and_no_url_use(self):
        for key, value in [
            ("file:size", True),
            ("file:size", 0),
            ("file:size", "123"),
            ("type", {"private": "DO_NOT_EXPOSE"}),
            ("roles", []),
        ]:
            with self.subTest(key=key), self.assertRaises(FetchError):
                parsed(result=changed(items(), ("features", 0, "assets", "Product", key), value))
        self.assertNotIn("DO_NOT_EXPOSE", json.dumps(parsed()))

    def test_malformed_nested_shapes_fail_closed(self):
        for path in [
            ("features",),
            ("features", 0),
            ("features", 0, "properties"),
            ("features", 0, "assets", "Product"),
            ("links",),
        ]:
            with self.subTest(path=path), self.assertRaises(FetchError):
                parsed(result=changed(items(), path, None))

    def test_response_provenance_and_input_validation(self):
        first, second = response(collection(), COLLECTION_URL), response(items(), SEARCH_URL)
        for field, value in [
            ("status", 304),
            ("status", 206),
            ("url", ITEMS_URL),
            ("headers", {}),
            ("headers", {"content-type": "text/html"}),
            ("body", b""),
            ("body", b" " * (MAX_BYTES + 1)),
            ("body", b'{"features":[],"features":[]}'),
            ("body", b'{"ignored":NaN}'),
            ("body", b'{"ignored":1e999}'),
            ("body", b'{"ignored":' + b"9" * 1000 + b"}"),
            ("body", b'{"ignored":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"),
            ("body", b"[]"),
            ("body", b"\xff"),
            ("body", b"{"),
        ]:
            bad = deepcopy(second)
            setattr(bad, field, value)
            with self.subTest(field=field, value_type=type(value)), self.assertRaises(FetchError):
                parse_metadata(first, bad)
        result = parse_metadata(first, second)
        self.assertEqual(
            [r["request_url"] for r in result["responses"]], [COLLECTION_URL, SEARCH_URL]
        )
        self.assertEqual(result["responses"][1]["bytes"], len(second.body))
        self.assertEqual(len(result["responses"][1]["sha256"]), 64)

    def test_fetch_cli_requests_only_two_fixed_metadata_urls(self):
        output = io.StringIO()
        with patch(
            "ingestion.clms_lc100.Client.get",
            side_effect=[
                response(collection(), COLLECTION_URL),
                response(items(), SEARCH_URL),
            ],
        ) as get:
            call_command("clms_lc100_preflight", fetch=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertEqual([c.args[0] for c in get.call_args_list], [COLLECTION_URL, SEARCH_URL])
        self.assertTrue(result["fetched"])
        self.assertIn("checked_at", result)
        self.assertNotIn("DO_NOT_EXPOSE", output.getvalue())

    def test_invalid_first_response_stops_before_second_request(self):
        with patch(
            "ingestion.clms_lc100.Client.get", return_value=response({}, COLLECTION_URL)
        ) as get:
            with self.assertRaises(FetchError):
                preflight(fetch=True)
        self.assertEqual(get.call_count, 1)

    def test_cli_failure_has_no_partial_or_private_output(self):
        output = io.StringIO()
        with patch(
            "ingestion.clms_lc100.Client.get",
            side_effect=[
                response(collection(), COLLECTION_URL),
                FetchError("DO_NOT_EXPOSE"),
            ],
        ):
            with self.assertRaises(CommandError) as caught:
                call_command("clms_lc100_preflight", fetch=True, stdout=output)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("DO_NOT_EXPOSE", str(caught.exception))


class Lc100TransportTests(SimpleTestCase):
    robots_url = "https://stac.dataspace.copernicus.eu/robots.txt"

    def make_transport(self, target=None, status=200, body=None, robots=b"User-agent: *\nAllow: /"):
        calls = []

        class FakeHttps(BaseHandler):
            handler_order = 100

            def https_open(self, req):
                calls.append(req.full_url)
                is_robots = req.full_url.endswith("/robots.txt")
                headers = Message()
                headers["Content-Type"] = "text/plain" if is_robots else "application/json"
                headers["Location"] = SEARCH_URL + "&redirect=1"
                payload = (
                    robots
                    if is_robots
                    else json.dumps(
                        collection() if req.full_url == COLLECTION_URL else items()
                    ).encode()
                )
                if req.full_url == target and body is not None:
                    payload = body
                result = addinfourl(
                    io.BytesIO(payload),
                    headers,
                    req.full_url,
                    status if req.full_url == target else 200,
                )
                result.msg = "Synthetic response"
                return result

        client = Client()
        client.opener.add_handler(FakeHttps())
        return client, calls

    def run_transport(self, client):
        with (
            patch("ingestion.clms_lc100.Client", return_value=client),
            patch("ingestion.transport.validate_url"),
            patch("ingestion.transport.time.sleep"),
        ):
            return preflight(fetch=True)

    def test_real_transport_has_only_robots_and_two_metadata_requests(self):
        client, calls = self.make_transport()
        result = self.run_transport(client)
        self.assertEqual(calls, [self.robots_url, COLLECTION_URL, SEARCH_URL])
        self.assertEqual(client.max_bytes, MAX_BYTES)
        self.assertEqual(result["result_state"], "found")
        self.assertFalse(result["resources_downloaded"])

    def test_all_redirects_on_either_endpoint_stop_before_destination(self):
        for target in (COLLECTION_URL, SEARCH_URL):
            for code in (301, 302, 303, 307, 308):
                client, calls = self.make_transport(target=target, status=code)
                with self.subTest(target=target, code=code), self.assertRaises(FetchError):
                    self.run_transport(client)
                expected = [self.robots_url, COLLECTION_URL]
                if target == SEARCH_URL:
                    expected.append(SEARCH_URL)
                self.assertEqual(calls, expected)

    def test_robots_disallow_stops_before_each_metadata_request(self):
        for path, expected in [
            ("/v1/collections/", [self.robots_url]),
            ("/v1/collections/" + COLLECTION_ID + "/items", [self.robots_url, COLLECTION_URL]),
        ]:
            client, calls = self.make_transport(
                robots=("User-agent: *\nDisallow: " + path).encode()
            )
            with self.subTest(path=path), self.assertRaises(FetchError):
                self.run_transport(client)
            self.assertEqual(calls, expected)

    def test_oversize_on_either_endpoint_is_rejected_without_assets(self):
        for target in (COLLECTION_URL, SEARCH_URL):
            client, calls = self.make_transport(target=target, body=b" " * (MAX_BYTES + 1))
            with self.subTest(target=target), self.assertRaises(FetchError):
                self.run_transport(client)
            self.assertEqual(calls[-1], target)
            self.assertLessEqual(len(calls), 3)

    def test_first_profile_failure_does_not_request_items(self):
        client, calls = self.make_transport(target=COLLECTION_URL, body=b"{}")
        with self.assertRaises(FetchError):
            self.run_transport(client)
        self.assertEqual(calls, [self.robots_url, COLLECTION_URL])
