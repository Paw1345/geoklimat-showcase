import io
import json
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.eu_authority_tables import CONFIG, FIELDS, SLUG, URL
from ingestion.inspire_vocabularies import CONFIG as PRIMARY_CONFIG
from ingestion.inspire_vocabularies import SLUG as PRIMARY_SLUG
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response

BASE = "http://publications.europa.eu/resource/authority/"


class EuAuthorityTablesTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="d" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            release=release,
            registry_id=180,
            master={},
            technical={},
            priority="P2",
            integration_state="needs_connector",
        )
        self.primary = Source.objects.create(
            slug=PRIMARY_SLUG, adapter="json", url=URL, config=PRIMARY_CONFIG.copy()
        )
        self.entry.sources.add(self.primary)
        self.source = Source.objects.create(
            slug=SLUG, adapter="json", url=URL, config=CONFIG.copy()
        )
        self.rows = [
            {
                "scheme": {"type": "uri", "value": BASE + "country"},
                "label": {"type": "literal", "xml:lang": "en", "value": "Countries"},
                "version": {"type": "literal", "value": "20250618-0"},
            },
            {
                "scheme": {"type": "uri", "value": BASE + "data-theme"},
                "label": {"type": "literal", "xml:lang": "en", "value": "Data theme"},
            },
        ]

    def http(self):
        client = Mock()
        client.get.side_effect = lambda url, headers: Response(
            200,
            json.dumps({"head": {"vars": FIELDS}, "results": {"bindings": self.rows}}).encode(),
            {},
            url,
        )
        return client

    def test_one_get_stable_digest_version_in_title_no_dates(self):
        client = self.http()
        entries, rejected, _, digest = fetch_entries(self.source, client)
        self.assertEqual(digest, fetch_entries(self.source, self.http())[3])
        self.assertEqual((len(entries), rejected, client.get.call_count), (2, 0, 1))
        self.assertEqual(entries[0].title, "Countries (wersja 20250618-0)")
        self.assertEqual(entries[0].metadata["table_version_raw"], "20250618-0")
        self.assertEqual(entries[1].title, "Data theme")
        self.assertFalse(entries[0].metadata["table_values_downloaded"])
        self.assertTrue(all(not e.published and not e.updated for e in entries))
        self.rows[0]["version"]["value"] = "20251001-0"
        self.assertNotEqual(digest, fetch_entries(self.source, self.http())[3])

    def test_duplicate_order_language_identity_and_version_fail_closed(self):
        original = json.loads(json.dumps(self.rows))
        for mutation in ["duplicate", "order", "language", "uri", "nested", "version"]:
            self.rows = json.loads(json.dumps(original))
            if mutation == "duplicate":
                self.rows[1] = {**self.rows[0], "version": {"type": "literal", "value": "2"}}
            if mutation == "order":
                self.rows.reverse()
            if mutation == "language":
                self.rows[0]["label"]["xml:lang"] = "fr"
            if mutation == "uri":
                self.rows[0]["scheme"]["value"] = "http://example.org/authority/country"
            if mutation == "nested":
                self.rows[1]["scheme"]["value"] = BASE + "country/sub/0001"
            if mutation == "version":
                self.rows[0]["version"]["value"] = "<script>"
            with self.subTest(mutation=mutation), self.assertRaises(FetchError):
                fetch_entries(self.source, self.http())

    def test_numbered_subset_scheme_is_accepted(self):
        self.rows.insert(
            1,
            {
                "scheme": {"type": "uri", "value": BASE + "country/0001"},
                "label": {"type": "literal", "xml:lang": "en", "value": "Current EU members"},
            },
        )
        self.rows.append(
            {
                "scheme": {"type": "uri", "value": BASE + "sdmxglossary2018"},
                "label": {"type": "literal", "xml:lang": "en", "value": "SDMX glossary"},
                "version": {"type": "literal", "xml:lang": "en", "value": "2018"},
            }
        )
        self.rows.append(
            {
                "scheme": {"type": "uri", "value": BASE + "sdmxglossary2018/cross_domain"},
                "label": {"type": "literal", "xml:lang": "en", "value": "Cross-domain"},
            }
        )
        entries = fetch_entries(self.source, self.http())[0]
        self.assertEqual([e.title for e in entries][1], "Current EU members")
        self.assertEqual(entries[3].title, "SDMX glossary (wersja 2018)")
        self.assertEqual(len(entries), 5)

    def test_empty_budget_and_foreign_config_refused(self):
        self.rows = []
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http())
        self.rows = [{}] * 1001
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http())
        self.source.config = {**CONFIG, "limit": 5000}
        client = self.http()
        with self.assertRaises(FetchError):
            fetch_entries(self.source, client)
        client.get.assert_not_called()

    def test_configure_dry_apply_idempotent_and_coexists_with_inspire_codes(self):
        self.source.delete()
        out = io.StringIO()
        call_command("configure_eu_authority_tables", stdout=out)
        self.assertFalse(Source.objects.filter(slug=SLUG).exists())
        call_command("configure_eu_authority_tables", apply=True, stdout=out)
        call_command("configure_eu_authority_tables", apply=True, stdout=out)
        source = Source.objects.get(slug=SLUG)
        self.assertEqual((source.enabled, source.activation_approved), (False, False))
        self.assertEqual(source.interval_minutes, 1440)
        self.assertTrue(self.entry.sources.filter(pk=source.pk).exists())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.integration_state, "needs_connector")
        # The INSPIRE code channels can still be prepared next to the new catalogue.
        call_command("configure_inspire_codes", apply=True, stdout=out)
        source.config = {"admin": True}
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_eu_authority_tables", apply=True, stdout=out)
        source.refresh_from_db()
        self.assertEqual(source.config, {"admin": True})

    def test_configure_refuses_foreign_link_or_missing_primary(self):
        self.source.delete()
        self.entry.sources.add(Source.objects.create(slug="other", adapter="rss", url=URL))
        with self.assertRaises(CommandError):
            call_command("configure_eu_authority_tables", apply=True, stdout=io.StringIO())
        self.assertFalse(Source.objects.filter(slug=SLUG).exists())
