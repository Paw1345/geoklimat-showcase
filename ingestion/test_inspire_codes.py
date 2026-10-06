import hashlib
import io
import json
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.inspire_codes import CONFIG, SCHEMES, SLUG, URL
from ingestion.inspire_vocabularies import CONFIG as PRIMARY_CONFIG
from ingestion.inspire_vocabularies import SLUG as PRIMARY_SLUG
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


def binding(value, language=None):
    return (
        {"type": "literal", "value": value, "xml:lang": language}
        if language
        else {
            "type": "uri",
            "value": value,
        }
    )


class InspireCodesTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=180,
            release=release,
            master={},
            technical={},
            priority="P2",
            integration_state="needs_connector",
        )
        self.primary = Source.objects.create(
            slug=PRIMARY_SLUG,
            adapter="json",
            url=URL,
            config=PRIMARY_CONFIG,
            enabled=True,
            activation_approved=True,
        )
        self.registry.sources.add(self.primary)
        self.source = Source(slug=SLUG, adapter="json", url=URL, config=CONFIG)
        self.rows = [
            {
                "scheme": binding(s),
                "concept": binding(s + "/example"),
                "label": binding("English label", "en"),
                "label_pl": binding("Nazwa polska", "pl"),
                "definition": binding("Source definition", "en"),
            }
            for s in sorted(SCHEMES)
        ]

    def http_client(self):
        client = Mock()
        client.get.side_effect = lambda url, headers: Response(
            200,
            json.dumps(
                {
                    "head": {"vars": ["scheme", "concept", "label", "label_pl", "definition"]},
                    "results": {"bindings": self.rows},
                }
            ).encode(),
            {},
            url,
        )
        return client

    def test_contract_one_get_raw_labels_no_dates_stable_identity(self):
        client = self.http_client()
        entries, rejected, response, digest = fetch_entries(self.source, client)
        self.assertEqual(len(entries), 8)
        self.assertEqual(rejected, 0)
        self.assertEqual(response.status, 200)
        client.get.assert_called_once()
        self.assertEqual(digest, fetch_entries(self.source, self.http_client())[3])
        self.assertTrue(all(not e.published and not e.updated for e in entries))
        self.assertEqual(entries[0].title, "Nazwa polska")
        self.assertFalse(entries[0].metadata["code_status_verified"])
        self.assertEqual(entries[0].metadata["record_kind"], "dataset_catalogue")
        self.rows[0]["label_pl"]["value"] = "Zmieniona nazwa"
        changed = fetch_entries(self.source, self.http_client())[0][0]
        self.assertEqual(entries[0].key, changed.key)
        self.assertNotEqual(entries[0].digest(), changed.digest())

    def test_missing_polish_and_definition_are_not_invented(self):
        del self.rows[0]["label_pl"]
        del self.rows[0]["definition"]
        row = fetch_entries(self.source, self.http_client())[0][0]
        self.assertEqual(row.title, "English label")
        self.assertEqual(row.metadata["display_language"], "en")
        self.assertEqual(row.metadata["label_pl"], "")
        self.assertEqual(row.summary, "")

    def test_order_duplicate_membership_and_language_fail_whole_batch(self):
        original = json.dumps(self.rows)
        for mutation in ["order", "duplicate", "membership", "language", "foreign", "extra"]:
            self.rows = json.loads(original)
            if mutation == "order":
                self.rows.reverse()
            if mutation == "duplicate":
                self.rows.insert(0, self.rows[0])
            if mutation == "membership":
                self.rows[0]["concept"] = binding("http://inspire.ec.europa.eu/theme/other")
            if mutation == "language":
                self.rows[0]["label_pl"]["xml:lang"] = "de"
            if mutation == "foreign":
                self.rows[0]["concept"] = binding("https://example.org/code")
            if mutation == "extra":
                self.rows[0]["date"] = binding("2026", "en")
            with self.subTest(mutation=mutation), self.assertRaises(FetchError):
                fetch_entries(self.source, self.http_client())

    def test_empty_missing_scheme_and_limit_refused(self):
        original = self.rows
        for rows in [[], original[:-1], original * 126]:
            self.rows = rows
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http_client())

    def test_profile_guard_before_http_and_response_identity(self):
        self.source.config = {**CONFIG, "limit": 2000}
        client = self.http_client()
        with self.assertRaises(FetchError):
            fetch_entries(self.source, client)
        client.get.assert_not_called()
        self.source.config = CONFIG
        client.get.side_effect = lambda url, headers: Response(200, b"{}", {}, URL)
        with self.assertRaises(FetchError):
            fetch_entries(self.source, client)

    def test_duplicate_json_keys_and_bad_head_refused(self):
        for payload in [
            b'{"head":{},"head":{}}',
            b"{}",
            b'{"head":{"vars":[]},"results":{"bindings":[]}}',
        ]:
            client = Mock()
            client.get.side_effect = lambda url, headers, body=payload: Response(200, body, {}, url)
            with self.assertRaises(FetchError):
                fetch_entries(self.source, client)

    def test_configure_dry_apply_repeat_preserves_primary_and_gap(self):
        output = io.StringIO()
        call_command("configure_inspire_codes", stdout=output)
        self.assertEqual(Source.objects.count(), 1)
        call_command("configure_inspire_codes", apply=True, stdout=output)
        source = Source.objects.get(slug=SLUG)
        self.assertFalse(source.enabled)
        self.assertFalse(source.activation_approved)
        source.last_audit = {"status": "verified", "count": 8}
        source.save()
        call_command("configure_inspire_codes", apply=True, stdout=output)
        source.refresh_from_db()
        self.assertEqual(source.last_audit, {"status": "verified", "count": 8})
        self.primary.refresh_from_db()
        self.assertTrue(self.primary.enabled)
        self.assertEqual(self.primary.config, PRIMARY_CONFIG)
        self.registry.refresh_from_db()
        self.assertEqual(self.registry.integration_state, "needs_connector")

    def test_configure_admin_or_other_link_refused(self):
        call_command("configure_inspire_codes", apply=True, stdout=io.StringIO())
        source = Source.objects.get(slug=SLUG)
        source.config = {"admin": True}
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_inspire_codes", apply=True, stdout=io.StringIO())
        source.config = CONFIG
        source.save()
        self.registry.sources.remove(source)
        with self.assertRaises(CommandError):
            call_command("configure_inspire_codes", apply=True, stdout=io.StringIO())

    def populate(self):
        self.source.activation_approved = True
        self.source.enabled = True
        self.source.save()
        entries = fetch_entries(self.source, self.http_client())[0]
        for entry in entries:
            Item.objects.create(
                source=self.source,
                external_id=entry.key,
                title=entry.title,
                url=entry.url,
                content_hash=entry.digest(),
                metadata={**entry.metadata, "private": "not exported"},
            )
        return entries

    def test_staff_get_ui_filter_and_allowlisted_export(self):
        self.populate()
        path = "/vocabularies/"
        self.assertEqual(self.client_web.get(path).status_code, 302)

    @property
    def client_web(self):
        return self.client

    def test_web_filters_exports_no_store_etag_and_no_raw_metadata(self):
        self.populate()
        staff = get_user_model().objects.create_user(username="operator", is_staff=True)
        self.client_web.force_login(staff)
        response = self.client_web.get("/vocabularies/")
        self.assertContains(response, "Nazwa polska")
        self.assertEqual(self.client_web.post("/vocabularies/").status_code, 405)
        self.assertEqual(self.client_web.get("/vocabularies/", {"scheme": "bad"}).status_code, 400)
        response = self.client_web.get(
            "/vocabularies/",
            {
                "scheme": sorted(SCHEMES)[0],
                "q": "English",
                "format": "json",
            },
        )
        self.assertEqual(response.json()["count"], 1)
        self.assertNotContains(response, "not exported")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"')
        self.source.activation_approved = False
        self.source.save()
        self.assertEqual(
            self.client_web.get("/vocabularies/", {"format": "json"}).json()["count"], 0
        )

    def test_html_definition_escaped_and_safe_pagination(self):
        self.rows[0]["definition"]["value"] = "<script>alert(1)</script>"
        self.populate()
        staff = get_user_model().objects.create_user(username="operator", is_staff=True)
        self.client_web.force_login(staff)
        response = self.client_web.get("/vocabularies/", {"page": "invalid"})
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
