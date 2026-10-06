import csv
import hashlib
import io
import json
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.http import QueryDict
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from core.collections import report
from ingestion.models import Item, Source


class CollectionTests(TransactionTestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="staff", is_staff=True)
        self.source = Source.objects.create(
            slug="collection-source",
            name="Public catalogue",
            enabled=True,
            activation_approved=True,
            config={"secret": "never-export-config"},
            owner="never-export-owner",
            url="https://example.org/catalog",
            last_audit={"error": "never-export-error"},
        )
        self.metadata = {
            "record_kind": "dataset_collection",
            "collection_id": "collection-a",
            "doi_raw": "10.1234/test",
            "license_raw": "proprietary",
            "extent_raw": {
                "spatial": {"bbox": [[0, -90, 360, 90]], "secret": "never-export-extent-extra"},
                "temporal": {"interval": [["2006-01-01T00:00:00Z", None]]},
                "assets": {"url": "never-export-extent-asset"},
            },
            "contacts": ["never-export-contact"],
            "assets": ["never-export-asset"],
            "description": "never-export-prose",
            "listing_scope": "example_scope",
        }
        self.item = self.create_item()
        self.client.force_login(self.staff)

    def create_item(self, **kwargs):
        values = {
            "source": self.source,
            "external_id": "a" * 64,
            "content_hash": "b" * 64,
            "title": "A collection",
            "url": "https://example.org/collection-a",
            "metadata": deepcopy(self.metadata),
        }
        values.update(kwargs)
        return Item.objects.create(**values)

    def test_staff_only_get_no_store_nosniff_and_escaped_html(self):
        Item.objects.filter(pk=self.item.pk).update(title="<script>alert(1)</script>")
        response = self.client.get("/collections/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "nie pobrane pomiary")
        self.assertEqual(self.client.post("/collections/").status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get("/collections/?format=json").status_code, 302)
        self.client.force_login(get_user_model().objects.create_user(username="reader"))
        self.assertEqual(self.client.get("/collections/?format=csv").status_code, 302)

    def test_whitelist_raw_metadata_and_explicit_scope_flags(self):
        response = self.client.get("/collections/?format=json")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["local_filtered_total"], 1)
        row = result["records"][0]
        self.assertEqual(row["provider_identifier"], "collection-a")
        self.assertEqual(row["doi_raw"], "10.1234/test")
        self.assertEqual(row["extent_raw"]["spatial"]["bbox"], [[0, -90, 360, 90]])
        self.assertEqual(row["extent_raw"]["temporal"]["interval"][0][1], None)
        self.assertFalse(row["license_permission_verified"])
        for key in (
            "full_provider_catalogues_verified",
            "resources_downloaded_by_this_view",
            "unique_products_verified",
            "current_30day_measurements_verified",
            "exact_pl_eu_mask_verified",
            "content_hash_recomputed",
            "same_provider_snapshot_across_pages_verified",
        ):
            self.assertIs(result[key], False)
        for secret in ("never-export-", "activation_approved", '"config"', '"owner"'):
            self.assertNotIn(secret, response.content.decode())
        self.assertEqual(response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"')

    def test_unknown_fields_remain_null_not_guessed_from_url_or_hash(self):
        Item.objects.filter(pk=self.item.pk).update(
            metadata={"record_kind": "dataset_collection", "license": "CC BY 4.0"}
        )
        row = self.client.get("/collections/?format=json").json()["records"][0]
        self.assertIsNone(row["provider_identifier"])
        self.assertIsNone(row["doi_raw"])
        self.assertIsNone(row["extent_raw"])
        self.assertEqual(row["license_indicator_raw"], "CC BY 4.0")

    def test_filters_title_identifier_source_exact_license_doi_missing(self):
        metadata = {
            "record_kind": "dataset_collection",
            "dataset_code": "env_air",
            "license": "other",
        }
        second = self.create_item(external_id="c" * 64, title="Other", metadata=metadata)
        cases = (
            ({"q": "collection-a"}, [self.item.pk]),
            ({"q": "env_air"}, [second.pk]),
            ({"license": "proprietary"}, [self.item.pk]),
            ({"license": "other"}, [second.pk]),
            ({"doi": "yes"}, [self.item.pk]),
            ({"doi": "no"}, [second.pk]),
            ({"q": "no-match"}, []),
            ({"source": self.source.pk}, [self.item.pk, second.pk]),
        )
        for filters, expected in cases:
            with self.subTest(filters=filters):
                result = self.client.get("/collections/", {**filters, "format": "json"}).json()
                self.assertEqual([r["item_id"] for r in result["records"]], expected)

    def test_revoked_sources_hidden_but_approved_disabled_history_visible(self):
        private = Source.objects.create(slug="revoked", activation_approved=False)
        self.create_item(source=private, external_id="d" * 64, title="Revoked hidden")
        self.create_item(external_id="e" * 64, title="News hidden", metadata={})
        Source.objects.filter(pk=self.source.pk).update(enabled=False)
        result = self.client.get("/collections/?format=json").json()
        self.assertEqual(result["local_filtered_total"], 1)
        self.assertFalse(result["records"][0]["source_enabled"])
        self.assertEqual(self.client.get("/collections/", {"source": private.pk}).status_code, 400)

    def test_stable_paging_selected_page_exports_and_links_preserve_filters(self):
        for index in range(3):
            self.create_item(external_id=str(index) * 64, title=f"A collection {index}")
        first = self.client.get("/collections/?format=json&page_size=2").json()
        second = self.client.get("/collections/?format=json&page_size=2&page=2").json()
        self.assertEqual((first["local_filtered_total"], first["pages"]), (4, 2))
        self.assertTrue(first["has_next"])
        self.assertTrue(second["has_previous"])
        self.assertFalse(second["has_next"])
        first_ids = {r["item_id"] for r in first["records"]}
        self.assertFalse(first_ids & {r["item_id"] for r in second["records"]})
        html = self.client.get(
            "/collections/", {"source": self.source.pk, "q": "A collection", "page_size": 2}
        )
        self.assertContains(html, "page=2")
        self.assertContains(html, f"source={self.source.pk}")
        self.assertContains(html, "q=A+collection")

    def test_invalid_or_duplicate_filters_fail_without_fallback(self):
        for query in (
            "page=0",
            "page=999",
            "page_size=101",
            "doi=maybe",
            "source=999999",
            "page=1&page=2",
            "format=xml",
            "owner=secret",
            "q=" + "x" * 101,
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/collections/?" + query).status_code, 400)

    def test_csv_formula_injection_escaping_and_partial_scope(self):
        Item.objects.filter(pk=self.item.pk).update(title=" =HYPERLINK(1)")
        response = self.client.get("/collections/?format=csv")
        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0]["title"], "' =HYPERLINK(1)")
        self.assertEqual(rows[0]["selected_page_only"], "True")
        self.assertEqual(rows[0]["full_provider_catalogues_verified"], "False")
        self.assertEqual(rows[0]["resources_downloaded_by_this_view"], "False")
        self.assertEqual(rows[0]["license_permission_verified"], "False")
        self.assertEqual(json.loads(rows[0]["extent_raw"])["spatial"]["bbox"][0][2], 360)
        self.assertEqual(response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"')

    def test_input_preflight_refuses_before_metadata_rows_read(self):
        with (
            patch("core.collections.INPUT_BYTES", 1),
            patch(
                "core.collections.record", side_effect=AssertionError("No records before budget")
            ),
        ):
            response = self.client.get("/collections/?format=json")
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("records", response.json())

    def test_invalid_metadata_or_output_budget_rejects_whole_page(self):
        self.create_item(
            external_id="f" * 64,
            metadata={
                "record_kind": "dataset_collection",
                "extent_raw": {"spatial": {"bbox": [["bad"]]}},
            },
        )
        response = self.client.get("/collections/?format=json")
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("records", response.json())
        Item.objects.filter(external_id="f" * 64).delete()
        for kind in ("html", "json", "csv"):
            with patch("core.collections.OUTPUT_BYTES", 1):
                self.assertEqual(
                    self.client.get("/collections/", {"format": kind}).status_code, 409
                )

    def test_no_http_or_writes_and_postgresql_read_only_transaction(self):
        before = list(Item.objects.values())
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.assertEqual(self.client.get("/collections/?format=json").status_code, 200)
        self.assertEqual(list(Item.objects.values()), before)

        def forbidden_write(row):
            Item.objects.filter(pk=self.item.pk).update(title="must not change")

        with patch("core.collections.record", side_effect=forbidden_write):
            with self.assertRaises(DatabaseError):
                report(QueryDict(""))
        self.item.refresh_from_db()
        self.assertEqual(self.item.title, "A collection")

    def test_projection_does_not_select_whole_metadata_or_source_configuration(self):
        with CaptureQueriesContext(connection) as queries:
            form, result = report(QueryDict("format=json"))
        self.assertTrue(form.is_valid())
        self.assertEqual(len(result["records"]), 1)
        self.assertLessEqual(len(queries), 8)
        selected = [q["sql"] for q in queries if '"ingestion_item"."title"' in q["sql"]]
        self.assertEqual(len(selected), 1)
        sql = selected[0]
        self.assertNotIn('"ingestion_source"."config"', sql)
        self.assertNotIn('"ingestion_source"."owner"', sql)
        self.assertNotIn('"ingestion_item"."metadata",', sql)
