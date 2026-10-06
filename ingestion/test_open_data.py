import io
import json
from datetime import timedelta
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.open_data import CONFIG, URL
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


class OpenDataTests(TestCase):
    def setUp(self):
        self.source = Source(adapter="json", url=URL, config=CONFIG.copy())
        self.client_http = Mock()

    def row(self, key="1", days=1, **extra):
        return {
            "id": key,
            "type": "dataset",
            "attributes": {
                "title": "Dane środowiskowe",
                "notes": "<p>Opis</p>",
                "modified": (timezone.now() - timedelta(days=days)).isoformat(),
                **extra,
            },
        }

    def response(self, rows, next_url=None):
        return Response(
            200, json.dumps({"data": rows, "links": {"next": next_url}}).encode(), {}, URL
        )

    def test_window_pagination_ignores_remote_url(self):
        self.client_http.get.side_effect = [
            self.response([self.row()], "https://evil.invalid/"),
            self.response([self.row("2", 31)], "anything"),
        ]
        entries, rejected, _, _ = fetch_entries(self.source, self.client_http)
        self.assertEqual((len(entries), rejected), (1, 0))
        self.assertEqual(entries[0].url, "https://dane.gov.pl/pl/dataset/1")
        self.assertFalse(entries[0].metadata["resources_downloaded"])
        self.assertIn(URL + "?", self.client_http.get.call_args.args[0])
        self.assertIn("page=2", self.client_http.get.call_args.args[0])

    def test_invalid_schema_dates_duplicates_order_and_budget(self):
        cases = [
            [self.response([self.row(modified="invalid")])],
            [self.response([self.row(), self.row()])],
            [self.response([self.row(days=2), self.row("2", days=1)])],
            [self.response([], "next")],
            [self.response([self.row()], "next")],
        ]
        self.source.config["max_pages"] = 1
        for responses in cases:
            with self.subTest(responses=responses):
                self.client_http.get.side_effect = responses
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.client_http)

    def test_traffic_counters_do_not_change_content_identity(self):
        row = self.row(views_count=1)
        self.client_http.get.return_value = self.response([row])
        first = fetch_entries(self.source, self.client_http)[0][0]
        row["attributes"]["views_count"] = 2
        self.client_http.get.return_value = self.response([row])
        second = fetch_entries(self.source, self.client_http)[0][0]
        self.assertEqual(first.digest(), second.digest())

    def test_configure_dry_apply_repeat_preserves_workbook_and_activation(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=24, release=release, master={"original": True}, technical={}
        )
        call_command("configure_open_data", stdout=io.StringIO())
        self.assertFalse(Source.objects.exists())
        call_command("configure_open_data", apply=True, stdout=io.StringIO())
        source = Source.objects.get()
        self.assertFalse(source.enabled or source.activation_approved)
        source.enabled = source.activation_approved = True
        source.save()
        call_command("configure_open_data", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        entry.refresh_from_db()
        self.assertTrue(source.enabled)
        self.assertEqual(entry.master, {"original": True})
        source.url = "https://api.dane.gov.pl/custom"
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_open_data", apply=True, stdout=io.StringIO())
