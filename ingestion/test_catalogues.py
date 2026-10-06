import io
import json
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.catalogue_profiles import CONFIG, PROFILES
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


class CatalogueTests(TestCase):
    def setUp(self):
        self.source = Source(adapter="stac", url=PROFILES[193], config=CONFIG.copy())
        self.http = Mock()

    def response(self, ids, total=None, next_url=None):
        data = {
            "collections": [{"id": key, "title": key, "license": "CC-BY-4.0"} for key in ids],
            "numberMatched": len(ids) if total is None else total,
            "numberReturned": len(ids),
            "links": [{"rel": "next", "href": next_url}] if next_url else [],
        }
        return Response(200, json.dumps(data).encode(), {}, self.source.url)

    def test_complete_public_catalogue_and_relative_pagination(self):
        self.http.get.side_effect = [
            self.response(["one"], 2, "?offset=1"),
            self.response(["two"], 2),
        ]
        entries, rejected, _, _ = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected), (2, 0))
        self.assertEqual(self.http.get.call_args.args[0], self.source.url + "?offset=1")
        self.assertEqual(entries[0].metadata["license"], "CC-BY-4.0")
        self.assertEqual(self.http.get.call_count, 2)  # No assets or retrieve jobs.

    def test_missing_page_and_changing_total_fail(self):
        cases = [
            [self.response(["one"], 2)],
            [self.response(["one"], 2, "?offset=1"), self.response(["two"], 3)],
            [self.response(["one"], 2, "?offset=1"), self.response(["one"], 2)],
        ]
        for replies in cases:
            with self.subTest(replies=replies):
                self.http.get.side_effect = replies
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.http)

    def test_unreviewed_next_path_rejected_before_fetch(self):
        self.http.get.return_value = self.response(["one"], 2, "/api/retrieve/v1/jobs")
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.assertEqual(self.http.get.call_count, 1)

    def test_declared_count_must_be_integer_and_match_page(self):
        for value in (None, "1", -1, True):
            reply = self.response(["one"])
            data = json.loads(reply.body)
            data["numberMatched"] = value
            reply.body = json.dumps(data).encode()
            self.http.get.return_value = reply
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)

    def test_configure_dry_apply_idempotent_and_no_admin_overwrite(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="a" * 64, sheets={})
        for key in PROFILES:
            RegistryEntry.objects.create(
                registry_id=key,
                release=release,
                priority="P0",
                master={"original": key},
                technical={},
                integration_state="needs_connector",
            )
        call_command("configure_copernicus_catalogues", stdout=io.StringIO())
        self.assertFalse(Source.objects.exists())
        call_command("configure_copernicus_catalogues", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 2)
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        Source.objects.update(enabled=True, activation_approved=True)
        call_command("configure_copernicus_catalogues", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.filter(enabled=True).count(), 2)
        self.assertEqual(RegistryEntry.objects.get(registry_id=193).master, {"original": 193})
        Source.objects.filter(slug="registry-194").update(url="https://example.invalid/custom")
        with self.assertRaises(CommandError):
            call_command("configure_copernicus_catalogues", apply=True, stdout=io.StringIO())
