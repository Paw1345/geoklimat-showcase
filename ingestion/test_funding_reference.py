import copy
import io
import json
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.funding_reference import profile
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


class FundingReferenceTests(TestCase):
    def test_registry_access_notes_are_read_only_and_do_not_claim_full_data(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="b" * 64, sheets={})
        for registry in [186, 189]:
            RegistryEntry.objects.create(
                release=release, registry_id=registry, master={}, technical={}
            )
        before = list(Source.objects.values())
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("Unexpected HTTP")):
            self.assertContains(self.client.get("/registry/186/"), "uprzedniej pisemnej zgody ECHA")
            self.assertContains(self.client.get("/registry/189/"), "ograniczoną diagnostykę")
            self.assertContains(self.client.get("/registry/189/"), "Bieżące nabory nadal")
            self.assertEqual(self.client.post("/registry/189/").status_code, 405)
        self.assertEqual(before, list(Source.objects.values()))

    def setUp(self):
        p = profile("LIFE2027")
        self.source = Source.objects.create(
            slug=p["slug"], adapter="json", url=p["url"], config=p["config"]
        )
        self.rows = [
            {
                "visible": True,
                "label": "Work programmes",
                "children": [
                    {
                        "visible": True,
                        "label": "LIFE Work Programme",
                        "urlExternal": "https://ec.europa.eu/info/funding-tenders/opportunities/docs/work_en.pdf",
                        "metadata": {
                            "title": "eUI needs it",
                            "published": "01-03-2026",
                            "versions": [],
                        },
                    }
                ],
            }
        ]

    def http(self):
        client = Mock()
        client.get.side_effect = lambda url, headers: Response(
            200, json.dumps(self.rows).encode(), {}, url
        )
        return client

    def test_contract_source_label_raw_date_no_child_requests_and_stable_digest(self):
        client = self.http()
        entries, rejected, _, digest = fetch_entries(self.source, client)
        self.assertEqual(rejected, 0)
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(entries[0].title, "LIFE Work Programme")
        self.assertFalse(entries[0].published or entries[0].updated)
        self.assertEqual(entries[0].metadata["reference_published_raw"], "01-03-2026")
        self.assertEqual(entries[0].metadata["record_kind"], "dataset_catalogue")
        self.assertFalse(entries[0].metadata["attachments_downloaded"])
        self.assertEqual(digest, fetch_entries(self.source, self.http())[3])
        self.rows[0]["children"][0]["metadata"]["published"] = "02-03-2026"
        self.assertNotEqual(digest, fetch_entries(self.source, self.http())[3])

    def test_same_link_in_different_categories_has_distinct_identity(self):
        second = copy.deepcopy(self.rows[0])
        second["label"] = "Guides"
        self.rows.append(second)
        entries = fetch_entries(self.source, self.http())[0]
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0].key, entries[1].key)
        self.rows[1]["label"] = "Work programmes"
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http())

    def test_hidden_parent_excludes_children_without_fake_empty_success(self):
        self.rows[0]["visible"] = False
        with self.assertRaisesMessage(FetchError, "empty"):
            fetch_entries(self.source, self.http())

    def test_supplier_unpublished_marker_preserved_without_date(self):
        self.rows[0]["children"][0]["metadata"]["published"] = "Not published yet"
        entry = fetch_entries(self.source, self.http())[0][0]
        self.assertEqual(entry.metadata["reference_published_raw"], "Not published yet")
        self.assertTrue(entry.metadata["reference_unpublished"])
        self.assertFalse(entry.published or entry.updated)

    def test_schema_dates_foreign_links_credentials_and_depth_fail_closed(self):
        original = copy.deepcopy(self.rows)
        for mutation in ["date", "metadata", "visibility", "foreign", "credentials", "depth"]:
            self.rows = copy.deepcopy(original)
            leaf = self.rows[0]["children"][0]
            if mutation == "date":
                leaf["metadata"]["published"] = "31-02-2026"
            if mutation == "metadata":
                leaf["metadata"] = []
            if mutation == "visibility":
                leaf["visible"] = "true"
            if mutation == "foreign":
                leaf["urlExternal"] = "https://example.org/data.pdf"
            if mutation == "credentials":
                leaf["urlExternal"] = "https://user@ec.europa.eu/a.pdf"
            if mutation == "depth":
                for _ in range(8):
                    self.rows = [{"label": "Nested", "visible": True, "children": self.rows}]
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http())

    def test_config_and_budget_reject_without_partial_success(self):
        self.source.config = {"mode": "funding_reference", "programme": "OTHER"}
        client = self.http()
        with self.assertRaises(FetchError):
            fetch_entries(self.source, client)
        client.get.assert_not_called()
        self.source.config = profile("LIFE2027")["config"]
        self.rows = self.rows * 1001
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http())

    def test_configure_dry_apply_idempotence_and_atomic_admin_preservation(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="d" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release,
            registry_id=189,
            master={},
            technical={},
            priority="P1",
            integration_state="needs_connector",
        )
        out = io.StringIO()
        call_command("configure_funding_reference", stdout=out)
        self.assertEqual(Source.objects.count(), 0)
        call_command("configure_funding_reference", apply=True, stdout=out)
        self.assertEqual(Source.objects.count(), 2)
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        self.assertFalse(Source.objects.filter(activation_approved=True).exists())
        call_command("configure_funding_reference", apply=True, stdout=out)
        self.assertEqual(Source.objects.count(), 2)
        entry.refresh_from_db()
        self.assertEqual(entry.integration_state, "needs_connector")
        source = Source.objects.get(slug=profile("HORIZON")["slug"])
        source.config = {"admin": True}
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_funding_reference", apply=True, stdout=out)
        source.refresh_from_db()
        self.assertEqual(source.config, {"admin": True})
