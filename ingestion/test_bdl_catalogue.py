import io
import json
from copy import deepcopy
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.bdl_catalogue import CONFIG, ORIGIN, SLUG, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import FetchError, Response


def catalogue():
    return {
        "links": [{"rel": "self", "href": URL}],
        "collections": [
            {
                "id": key,
                "title": "Forest " + key,
                "description": "Description",
                "itemType": "feature",
                "links": [
                    {"rel": "self", "href": ORIGIN + "/collections/" + key + "?f=json"},
                    {"rel": "canonical", "href": "http://www.naturalearthdata.com/"},
                ],
            }
            for key in ("rdlp", "nadlesnictwa")
        ],
    }


class BdlCatalogueTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=SLUG,
            adapter="json",
            url=URL,
            config=CONFIG,
            enabled=True,
            activation_approved=True,
        )
        self.http = Mock()
        self.data = catalogue()
        self.respond()

    def respond(self):
        self.http.get.return_value = Response(200, json.dumps(self.data).encode(), {}, URL)

    def test_single_get_metadata_only_no_demo_links_or_dates(self):
        entries, bad, response, digest = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), bad, response.status), (2, 0, 200))
        self.assertEqual(len(digest), 64)
        self.http.get.assert_called_once_with(URL, {})
        for entry in entries:
            self.assertEqual((entry.published, entry.updated), ("", ""))
            self.assertEqual(entry.metadata["record_kind"], "dataset_catalogue")
            self.assertFalse(entry.metadata["geometry_downloaded"])
            self.assertFalse(entry.metadata["reuse_permission_confirmed"])
            self.assertNotIn("naturalearth", json.dumps(entry.metadata))
            self.assertTrue(entry.url.startswith(ORIGIN + "/collections/"))

    def test_schema_identity_duplicate_pagination_fail_closed(self):
        original = deepcopy(self.data)
        for mutation in (
            lambda d: d.update(collections=[]),
            lambda d: d.update(collections=[d["collections"][0]] * 101),
            lambda d: d["collections"].append(d["collections"][0]),
            lambda d: d["links"].append({"rel": "next", "href": URL}),
            lambda d: d["collections"][0].update(id="../escape"),
            lambda d: d["collections"][0].update(links=[]),
            lambda d: d["collections"][0].update(itemType="unknown"),
            lambda d: d["collections"][0].update(title={}),
        ):
            with self.subTest(mutation=mutation):
                self.data = deepcopy(original)
                mutation(self.data)
                self.respond()
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.http)

    def test_endpoint_and_profile_drift_rejected(self):
        self.http.get.return_value.url = ORIGIN + "/"
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.respond()
        self.source.config = {**CONFIG, "admin": True}
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)

    def test_conditional_304_does_not_parse_empty_body(self):
        self.source.etag = '"version"'
        self.http.get.return_value = Response(304, b"", {}, URL)
        entries, bad, response, digest = fetch_entries(self.source, self.http, conditional=True)
        self.assertEqual((entries, bad, response.status, digest), ([], 0, 304, ""))
        self.http.get.assert_called_once_with(URL, {"If-None-Match": '"version"'})

    def test_import_repeat_and_separate_publications(self):
        with patch("ingestion.service.Client", return_value=self.http):
            run_batch(source_slug=SLUG, force=True)
            run_batch(source_slug=SLUG, force=True)
        self.assertEqual(Item.objects.filter(source=self.source).count(), 2)
        self.assertEqual(
            Item.objects.filter(source=self.source).values("publication_id").distinct().count(), 2
        )
        self.assertEqual(self.source.runs.latest("pk").unchanged, 2)

    def test_configure_dry_apply_repeat_preserves_registry_and_admin_state(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="fixture", sha256="bdl", sheets={})
        row = RegistryEntry.objects.create(
            registry_id=39,
            release=release,
            master={"original": True},
            technical={},
            integration_state="needs_connector",
        )
        call_command("configure_bdl_catalogue", stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 0)
        call_command("configure_bdl_catalogue", apply=True, stdout=io.StringIO())
        source = Source.objects.get(slug=SLUG)
        self.assertFalse(source.enabled)
        self.assertFalse(source.activation_approved)
        source.enabled = True
        source.activation_approved = True
        source.interval_minutes = 720
        source.save()
        call_command("configure_bdl_catalogue", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertEqual(
            (source.enabled, source.activation_approved, source.interval_minutes), (True, True, 720)
        )
        row.refresh_from_db()
        self.assertEqual(row.integration_state, "needs_connector")
        self.assertEqual(row.master, {"original": True})
        source.config = {"admin": True}
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_bdl_catalogue", apply=True, stdout=io.StringIO())

    def test_missing_registry_refuses_configuration(self):
        self.source.delete()
        with self.assertRaises(CommandError):
            call_command("configure_bdl_catalogue", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 0)
