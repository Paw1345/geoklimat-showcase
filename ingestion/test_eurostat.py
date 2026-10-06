import csv
import io
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.eurostat import CONFIG, HEADER, URL, fetch_catalogue
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.registry_profiles import profile
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


class EurostatTests(TestCase):
    def setUp(self):
        self.source = Source(adapter="watch", url=URL, config=CONFIG.copy())
        self.rows = [
            ["Database", "data", "folder", " ", " ", " ", " ", ""],
            ["    Environment", "env", "folder", " ", " ", " ", " ", ""],
            [
                "        Greenhouse emissions",
                "env_air",
                "dataset",
                "01.09.2026",
                "02.09.2026",
                "1990",
                "2025",
                "100",
            ],
            ["    Unrelated", "other", "folder", " ", " ", " ", " ", ""],
            [
                "        Not environment",
                "other_table",
                "table",
                "01.09.2026",
                "02.09.2026",
                "1990",
                "2025",
                "12",
            ],
        ]

    def client_for(self, rows=None, header=None):
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter="\t")
        writer.writerows(
            [HEADER if header is None else header, *(self.rows if rows is None else rows)]
        )
        client = Mock()
        client.get.return_value = Response(200, buffer.getvalue().encode(), {}, URL)
        return client

    def test_subtree_dates_identity_and_no_resource_requests(self):
        client = self.client_for()
        entries, rejected, _, _ = fetch_entries(self.source, client)
        self.assertEqual((len(entries), rejected), (1, 0))
        entry = entries[0]
        self.assertEqual(entry.title, "Greenhouse emissions")
        self.assertEqual(entry.published, "")
        self.assertEqual(entry.updated, "2026-09-02T00:00:00+00:00")
        self.assertFalse(entry.metadata["resources_downloaded"])
        client.get.assert_called_once_with(URL)
        self.rows[2][3] = "03.09.2026"
        changed = fetch_catalogue(self.source, self.client_for())[0][0]
        self.assertEqual(entry.key, changed.key)
        self.assertNotEqual(entry.digest(), changed.digest())

    def test_duplicates_deduplicated_but_conflicts_fail(self):
        rows = self.rows[:3] + [self.rows[2].copy()]
        self.assertEqual(len(fetch_catalogue(self.source, self.client_for(rows))[0]), 1)
        rows[-1][-1] = "101"
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client_for(rows))

    def test_fail_closed_on_header_hierarchy_missing_subtree_and_date(self):
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client_for(header=["changed"]))
        for index, field, value in [
            (1, 0, "            Broken"),
            (1, 1, "absent"),
            (2, 3, "31.02.2026"),
            (2, 1, "../../bad"),
        ]:
            rows = [row.copy() for row in self.rows]
            rows[index][field] = value
            with self.subTest(value=value), self.assertRaises(FetchError):
                fetch_catalogue(self.source, self.client_for(rows))

    def test_endpoint_redirect_and_truncation_rejected(self):
        client = self.client_for()
        client.get.return_value.url = "https://ec.europa.eu/"
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, client)
        client = self.client_for()
        client.get.return_value.body = client.get.return_value.body.rstrip()
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, client)
        self.source.config = {"mode": "eurostat_toc", "roots": ["data"]}
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client_for())

    def test_configure_dry_apply_repeat_and_admin_preservation(self):
        release = RegistryRelease.objects.create(filename="test", sha256="d" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=184, release=release, priority="P1", master={}, technical={}
        )
        call_command("configure_eurostat", stdout=io.StringIO())
        self.assertFalse(Source.objects.exists())
        call_command("configure_eurostat", apply=True, stdout=io.StringIO())
        source = entry.sources.get()
        self.assertFalse(source.enabled)
        source.enabled = source.activation_approved = True
        source.save()
        call_command("configure_eurostat", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertTrue(source.enabled)
        source.url = "https://example.org/admin"
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_eurostat", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertEqual(source.url, "https://example.org/admin")
        route, state, _ = profile({"ID": 184}, {})
        self.assertEqual(route, ("watch", URL, CONFIG))
        self.assertEqual(state, "configured")
