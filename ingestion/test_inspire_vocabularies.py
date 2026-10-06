import io
import json
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.inspire_vocabularies import CONFIG, SLUG, URL
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


class InspireVocabularyTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="c" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            release=release,
            registry_id=180,
            master={},
            technical={},
            priority="P2",
            integration_state="needs_connector",
        )
        self.source = Source.objects.create(
            slug=SLUG, adapter="json", url=URL, config=CONFIG.copy()
        )
        self.entry.sources.add(self.source)
        self.rows = [
            {
                "scheme": {"type": "uri", "value": "http://inspire.ec.europa.eu/codelist/" + key},
                "label": {"type": "literal", "xml:lang": "en", "value": key},
            }
            for key in ["Alpha", "Beta"]
        ]

    def http(self):
        client = Mock()

        def get(url, headers):
            data = {
                "head": {"vars": ["scheme", "label", "description"]},
                "results": {"bindings": self.rows},
            }
            return Response(200, json.dumps(data).encode(), {}, url)

        client.get.side_effect = get
        return client

    def test_service_contract_stable_digest_no_dates_or_child_requests(self):
        client = self.http()
        first = fetch_entries(self.source, client)
        second = fetch_entries(self.source, self.http())
        entries, rejected, response, digest = first
        self.assertEqual(digest, second[3])
        self.assertEqual(len(entries), 2)
        self.assertEqual(rejected, 0)
        self.assertEqual(client.get.call_count, 1)
        self.assertTrue(all(not e.published and not e.updated for e in entries))
        self.assertFalse(entries[0].metadata["code_values_downloaded"])
        self.assertEqual(entries[0].metadata["record_kind"], "dataset_catalogue")
        self.assertEqual(entries[0].url, self.rows[0]["scheme"]["value"])

    def test_duplicate_order_language_and_foreign_identity_fail_closed(self):
        original = json.loads(json.dumps(self.rows))
        for mutation in ["duplicate", "order", "language", "uri"]:
            self.rows = json.loads(json.dumps(original))
            if mutation == "duplicate":
                self.rows[1] = self.rows[0]
            if mutation == "order":
                self.rows.reverse()
            if mutation == "language":
                self.rows[0]["label"]["xml:lang"] = "pl"
            if mutation == "uri":
                self.rows[0]["scheme"]["value"] = "http://example.org/Alpha"
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http())

    def test_empty_or_truncated_budget_and_config_refused(self):
        self.rows = []
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http())
        self.rows = [{}] * 1001
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http())
        self.source.config = {"mode": "inspire_schemes", "language": "pl"}
        client = self.http()
        with self.assertRaises(FetchError):
            fetch_entries(self.source, client)
        client.get.assert_not_called()

    def test_configure_dry_apply_idempotent_and_admin_preserved(self):
        self.source.delete()
        out = io.StringIO()
        call_command("configure_inspire_vocabularies", stdout=out)
        self.assertFalse(Source.objects.exists())
        call_command("configure_inspire_vocabularies", apply=True, stdout=out)
        call_command("configure_inspire_vocabularies", apply=True, stdout=out)
        source = Source.objects.get(slug=SLUG)
        self.assertFalse(source.enabled)
        self.assertFalse(source.activation_approved)
        source.config = {"admin": True}
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_inspire_vocabularies", apply=True, stdout=out)
        source.refresh_from_db()
        self.assertEqual(source.config, {"admin": True})
