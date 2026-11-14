import json
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from ingestion import marine
from ingestion.transport import FetchError, Response


class MarinePartialParserTests(SimpleTestCase):
    def setUp(self):
        self.source = SimpleNamespace(
            adapter="stac",
            url=marine.URL,
            config=marine.CONFIG.copy(),
            slug="registry-195",
            enabled=True,
            activation_approved=True,
            registry_entries=Mock(),
        )
        self.source.registry_entries.filter.return_value.exists.return_value = True
        self.http = Mock()
        self.product = {
            "type": "Collection",
            "id": "P1",
            "title": "Sea",
            "license": "CC-BY-4.0",
            "properties": {"creationDate": "2026-09-01T00:00:00Z"},
        }

    def setup_replies(self, second):
        root = {
            "type": "Catalog",
            "id": "MDS",
            "links": [{"rel": "child", "href": f"{key}/product.stac.json"} for key in ("P1", "P2")],
        }
        records = [self.product, {**self.product, "id": "P2", **second}]
        self.http.get.side_effect = [Response(200, json.dumps(root).encode(), {}, marine.URL)] + [
            Response(
                200,
                json.dumps(row).encode(),
                {},
                marine.product_url(f"{row['id']}/product.stac.json"),
            )
            for row in records
        ]

    def test_partial_collects_valid_products_and_reports_missing_fields(self):
        for values, missing in (
            ({"title": None}, ("title",)),
            ({"license": ""}, ("license",)),
            ({"title": "", "license": None}, ("title", "license")),
            ({"title": " \t"}, ("title",)),
            ({"license": " \n"}, ("license",)),
        ):
            with self.subTest(values=values):
                self.setup_replies(values)
                result = marine.collect_catalogue(self.source, self.http, allow_partial=True)
                self.assertEqual([entry.metadata["product_id"] for entry in result.entries], ["P1"])
                self.assertEqual(result.rejected, 1)
                self.assertEqual(result.issues[0].product_id, "P2")
                self.assertEqual(result.issues[0].missing_fields, missing)
                self.assertEqual(result.declared_count, 2)
                self.assertEqual(result.inspected_count, 2)

    def test_fatal_invalid_metadata_even_when_a_field_is_missing(self):
        for values in (
            {"title": 1},
            {"title": "", "license": []},
            {"title": None, "properties": {"creationDate": "invalid"}},
            {"title": None, "properties": {"creationDate": []}},
            {"title": None, "properties": None},
        ):
            with self.subTest(values=values):
                self.setup_replies(values)
                with self.assertRaises(FetchError):
                    marine.collect_catalogue(self.source, self.http, allow_partial=True)

    def test_no_valid_products_fails(self):
        self.product["title"] = None
        self.setup_replies({"license": ""})
        with self.assertRaises(FetchError):
            marine.collect_catalogue(self.source, self.http, allow_partial=True)

    def test_fatal_error_after_valid_product_discards_entire_result(self):
        self.setup_replies({})
        responses = list(self.http.get.side_effect)
        self.http.get.side_effect = [responses[0], responses[1], FetchError("timeout")]
        with self.assertRaises(FetchError):
            marine.collect_catalogue(self.source, self.http, allow_partial=True)
        self.setup_replies({})
        responses = list(self.http.get.side_effect)
        responses[2].body = b"not json"
        self.http.get.side_effect = responses
        with self.assertRaises(ValueError):
            marine.collect_catalogue(self.source, self.http, allow_partial=True)

    def test_duplicate_and_above_budget_abort_before_products(self):
        for links in (
            [{"rel": "child", "href": "P1/product.stac.json"}] * 2,
            [{"rel": "child", "href": f"P{i}/product.stac.json"} for i in range(501)],
        ):
            with self.subTest(count=len(links)):
                self.http.reset_mock()
                body = json.dumps({"type": "Catalog", "id": "MDS", "links": links}).encode()
                self.http.get.side_effect = [Response(200, body, {}, marine.URL)]
                with self.assertRaises(FetchError):
                    marine.collect_catalogue(self.source, self.http, allow_partial=True)
                self.assertEqual(self.http.get.call_count, 1)

    def test_old_entry_point_remains_strict_even_for_extended_config(self):
        self.source.config = {**marine.CONFIG, "incomplete_policy": "missing_metadata_v1"}
        with self.assertRaises(FetchError):
            marine.fetch_catalogue(self.source, self.http)
        self.http.get.assert_not_called()

    def test_unreviewed_source_is_rejected_before_http(self):
        for field, value in (
            ("slug", "other"),
            ("adapter", "rss"),
            ("enabled", False),
            ("activation_approved", False),
        ):
            with self.subTest(field=field):
                old = getattr(self.source, field)
                setattr(self.source, field, value)
                with self.assertRaises(FetchError):
                    marine.collect_catalogue(self.source, self.http, allow_partial=True)
                self.http.get.assert_not_called()
                setattr(self.source, field, old)
        self.source.registry_entries.filter.return_value.exists.return_value = False
        with self.assertRaises(FetchError):
            marine.collect_catalogue(self.source, self.http, allow_partial=True)
        self.http.get.assert_not_called()

    def test_strict_result_stays_four_tuple_and_strict_fail_closed(self):
        self.setup_replies({})
        self.assertEqual(len(marine.fetch_catalogue(self.source, self.http)), 4)
        self.setup_replies({"title": None})
        with self.assertRaises(FetchError):
            marine.fetch_catalogue(self.source, self.http)
