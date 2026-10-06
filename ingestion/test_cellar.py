import io
import json
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.cellar import CONFIG, URL, XSD_DATE, fetch_metadata
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.registry_profiles import profile
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


@patch("ingestion.cellar.timezone.now", return_value=datetime(2026, 9, 30, tzinfo=UTC))
class CellarTests(TestCase):
    def row(self, number=0):
        return {
            "work": {
                "type": "uri",
                "value": f"http://publications.europa.eu/resource/cellar/00000000-0000-0000-0000-{number:012x}",
            },
            "date": {"type": "literal", "datatype": XSD_DATE, "value": "2026-09-01"},
            "title": {"type": "literal", "xml:lang": "en", "value": "Environmental document"},
        }

    def fetch(self, pages):
        client = Mock()
        client.get.side_effect = lambda url, **kwargs: Response(
            200,
            json.dumps(
                {
                    "head": {"vars": ["work", "date", "title"]},
                    "results": {"bindings": pages.pop(0)},
                }
            ).encode(),
            {},
            url,
        )
        result = fetch_metadata(Source(adapter="json", url=URL, config=CONFIG.copy()), client)
        return result, client

    def test_document_date_is_not_publication_or_update(self, now):
        (entries, rejected, response), client = self.fetch([[self.row()]])
        self.assertEqual(rejected, 0)
        self.assertEqual(entries[0].published, "")
        self.assertEqual(entries[0].updated, "")
        self.assertEqual(entries[0].metadata["document_date"], "2026-09-01")
        self.assertEqual(entries[0].metadata["eurovoc"], ["2470"])
        self.assertFalse(entries[0].metadata["resources_downloaded"])
        self.assertTrue(
            entries[0].url.startswith("https://publications.europa.eu/resource/cellar/")
        )

    def test_service_contract_and_stable_digest(self, now):
        (entries, rejected, response), _ = self.fetch([[self.row()]])
        source = Source(adapter="json", url=URL, config=CONFIG.copy())
        with patch("ingestion.cellar.fetch_metadata", return_value=(entries, rejected, response)):
            result = fetch_entries(source, Mock(), conditional=False)
            repeated = fetch_entries(source, Mock())
        self.assertEqual(len(result), 4)
        self.assertEqual(len(result[3]), 64)
        self.assertEqual(result[3], repeated[3])

    def test_pagination_empty_window_and_budget(self, now):
        (entries, _, _), client = self.fetch([[self.row(i) for i in range(50)], [self.row(50)]])
        self.assertEqual(len(entries), 51)
        self.assertIn("OFFSET+50", client.get.call_args.args[0])
        (entries, _, _), _ = self.fetch([[]])
        self.assertEqual(entries, [])
        with self.assertRaisesMessage(FetchError, "page budget"):
            self.fetch([[self.row(p * 50 + i) for i in range(50)] for p in range(10)])

    def test_bad_bindings_and_duplicates_reject_entire_batch(self, now):
        for field, key, value in (
            ("work", "value", "https://outside.example/resource"),
            ("date", "value", "2026-08-31"),
            ("date", "value", "2026-10-01"),
            ("date", "datatype", "string"),
            ("title", "xml:lang", "pl"),
            ("title", "value", ""),
        ):
            row = deepcopy(self.row())
            row[field][key] = value
            with self.subTest(field=field, key=key), self.assertRaises(FetchError):
                self.fetch([[row]])
        for rows in ([self.row(), self.row()], [self.row(1), self.row(0)]):
            with self.assertRaises(FetchError):
                self.fetch([rows])

    def test_changed_scope_or_redirect_is_rejected(self, now):
        client = Mock()
        with self.assertRaises(FetchError):
            fetch_metadata(
                Source(adapter="json", url=URL, config={**CONFIG, "eurovoc": "other"}), client
            )
        client.get.side_effect = lambda url, **kwargs: Response(200, b"{}", {}, URL)
        with self.assertRaisesMessage(FetchError, "endpoint"):
            fetch_metadata(Source(adapter="json", url=URL, config=CONFIG), client)

    def test_configuration_is_dry_idempotent_and_preserves_admin(self, now):
        release = RegistryRelease.objects.create(filename="test", sha256="c" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=170, release=release, priority="P1", master={}, technical={}
        )
        self.assertEqual(profile({"ID": 170}, {})[0], ("json", URL, CONFIG))
        call_command("configure_cellar", stdout=io.StringIO())
        self.assertFalse(Source.objects.exists())
        call_command("configure_cellar", apply=True, stdout=io.StringIO())
        call_command("configure_cellar", apply=True, stdout=io.StringIO())
        s = Source.objects.get(slug="registry-170")
        self.assertFalse(s.enabled)
        self.assertFalse(s.activation_approved)
        self.assertEqual(entry.sources.count(), 1)
        s.url = "https://example.org/admin"
        s.save()
        with self.assertRaises(CommandError):
            call_command("configure_cellar", apply=True, stdout=io.StringIO())
