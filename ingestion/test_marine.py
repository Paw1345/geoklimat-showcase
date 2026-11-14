import io
import json
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.marine import CONFIG, URL, product_url
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, ingest_source
from ingestion.transport import FetchError, Response


class MarineTests(TestCase):
    def setUp(self):
        self.source = Source(adapter="stac", url=URL, config=CONFIG.copy())
        self.http = Mock()
        self.product = {
            "type": "Collection",
            "id": "PRODUCT_1",
            "title": "Sea temperature",
            "description": "Public description",
            "license": "proprietary",
            "properties": {"creationDate": "2026-09-01T00:00:00Z"},
            "links": [{"rel": "item", "href": "https://example.org/asset"}],
        }

    def replies(self, links=None, product=None):
        root = {
            "type": "Catalog",
            "id": "MDS",
            "links": links
            if links is not None
            else [{"rel": "child", "href": "PRODUCT_1/product.stac.json"}],
        }
        return [
            Response(200, json.dumps(root).encode(), {}, URL),
            Response(
                200,
                json.dumps(product or self.product).encode(),
                {},
                product_url("PRODUCT_1/product.stac.json"),
            ),
        ]

    def test_products_only_no_asset_or_contact_retrieval(self):
        self.http.get.side_effect = self.replies()
        entries, rejected, _, digest = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected), (1, 0))
        self.assertEqual(entries[0].metadata["product_id"], "PRODUCT_1")
        self.assertFalse(entries[0].metadata["resources_downloaded"])
        self.assertEqual(self.http.get.call_count, 2)
        self.product["properties"]["contacts"] = ["not stored"]
        self.http.get.side_effect = self.replies()
        self.assertEqual(fetch_entries(self.source, self.http)[3], digest)
        self.product["license"] = "CC-BY-4.0"
        self.http.get.side_effect = self.replies()
        self.assertNotEqual(fetch_entries(self.source, self.http)[3], digest)

    def test_invalid_root_or_product_and_identity_fail_closed(self):
        for value in (
            [],
            {"type": "Collection", "id": "other"},
            dict(self.product, properties={"creationDate": "invalid"}),
        ):
            replies = self.replies()
            replies[1].body = json.dumps(value).encode()
            self.http.get.side_effect = replies
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)
        self.http.get.side_effect = [Response(200, b"[]", {}, URL)]
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)

    def test_missing_metadata_names_product_and_fields_without_remote_values(self):
        for fields in (("properties",), ("title",), ("license",), ("title", "license")):
            product = {**self.product, "description": "PRIVATE_REMOTE_TEXT"}
            for field in fields:
                product.pop(field)
            self.http.get.side_effect = self.replies(product=product)
            with self.subTest(fields=fields):
                with self.assertRaises(FetchError) as caught:
                    fetch_entries(self.source, self.http)
                self.assertEqual(
                    str(caught.exception),
                    f"Marine product metadata missing ({', '.join(fields)}): PRODUCT_1",
                )
                self.assertEqual(caught.exception.status, 200)
                self.assertEqual(caught.exception.url, product_url("PRODUCT_1/product.stac.json"))
                self.assertNotIn("PRIVATE_REMOTE_TEXT", str(caught.exception))

    def test_empty_metadata_and_wrong_properties_type_still_fail_closed(self):
        for values, field in (
            ({"title": ""}, "title"),
            ({"license": ""}, "license"),
            ({"properties": None}, "properties"),
            ({"properties": []}, "properties"),
        ):
            self.http.get.side_effect = self.replies(product={**self.product, **values})
            with self.subTest(values=values):
                with self.assertRaises(FetchError) as caught:
                    fetch_entries(self.source, self.http)
                self.assertEqual(
                    str(caught.exception), f"Marine product metadata missing ({field}): PRODUCT_1"
                )

    def test_incomplete_product_aborts_batch_preserving_existing_items(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="8" * 64, sheets={})
        self.source.registry = RegistryEntry.objects.create(
            registry_id=195, release=release, master={}, technical={}
        )
        self.source.slug = "registry-195"
        self.source.enabled = self.source.activation_approved = True
        self.source.save()
        Item.objects.create(
            source=self.source,
            external_id="older",
            title="Existing product",
            url=URL,
            content_hash="a" * 64,
            metadata={"product_id": "OLDER_PRODUCT"},
        )
        before = list(self.source.items.values())
        root = {
            "type": "Catalog",
            "id": "MDS",
            "links": [
                {"rel": "child", "href": f"PRODUCT_{i}/product.stac.json"} for i in (1, 2, 3)
            ],
        }
        incomplete = {
            "type": "Collection",
            "id": "PRODUCT_2",
            "properties": {"admp_updated": "2026-10-05"},
        }
        self.http.get.side_effect = [
            Response(200, json.dumps(root).encode(), {}, URL),
            self.replies()[1],
            Response(
                200, json.dumps(incomplete).encode(), {}, product_url("PRODUCT_2/product.stac.json")
            ),
        ]
        run = ingest_source(self.source, self.http)
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.http_status, 200)
        self.assertEqual(run.error, "Marine product metadata missing (title, license): PRODUCT_2")
        self.assertEqual((run.created, run.updated, run.unchanged), (0, 0, 0))
        self.assertEqual(list(self.source.items.values()), before)
        self.assertEqual(self.http.get.call_count, 3)

    def test_duplicate_empty_paged_and_external_children_rejected_before_fetch(self):
        child = {"rel": "child", "href": "PRODUCT_1/product.stac.json"}
        for links in (
            [],
            [child, child],
            [child, {"rel": "next"}],
            [{"rel": "child", "href": "https://example.org/product.stac.json"}],
        ):
            self.http.reset_mock()
            self.http.get.side_effect = self.replies(links)
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)
            self.assertEqual(self.http.get.call_count, 1)

    def test_configure_dry_apply_preserves_audit_and_admin(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="9" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=195, release=release, priority="P0", master={"original": 195}, technical={}
        )
        call_command("configure_marine", stdout=io.StringIO())
        self.assertFalse(Source.objects.exists())
        call_command("configure_marine", apply=True, stdout=io.StringIO())
        source = Source.objects.get()
        self.assertFalse(source.enabled)
        Source.objects.update(enabled=True, activation_approved=True)
        call_command("configure_marine", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertTrue(source.enabled)
        entry.refresh_from_db()
        self.assertEqual(entry.master, {"original": 195})
        source.url = "https://example.org/custom"
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_marine", apply=True, stdout=io.StringIO())
