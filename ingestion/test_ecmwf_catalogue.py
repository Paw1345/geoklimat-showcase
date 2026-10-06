import hashlib
import io
import json
from copy import deepcopy
from unittest.mock import MagicMock, Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.ecmwf_catalogue import CONFIG, MAX_BYTES, SLUG, URL, parse_catalogue
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import Client, FetchError, Response


def collection(key="tigge-forecasts"):
    return {
        "type": "Collection",
        "id": key,
        "stac_version": "1.1.0",
        "title": "TIGGE global ensemble forecast catalogue",
        "license": "other",
        "published": "2026-04-01T00:00:00Z",
        "updated": "2026-10-04T00:00:00Z",
        "sci:doi": "10.21957/8511726f",
        "keywords": ["Spatial coverage: Global"],
        "extent": {
            "spatial": {"bbox": [[0.0, -90.0, 360.0, 90.0]]},
            "temporal": {"interval": [["2006-10-01T00:00:00+00:00", "2026-11-17T00:00:00Z"]]},
        },
        "links": [
            {"rel": "self", "href": URL + "/" + key},
            {"rel": "items", "href": "https://example.org/items"},
        ],
        "description": "Full source prose must not be stored",
        "assets": {"data": {"href": "https://example.org/file.grib"}},
        "contacts": [{"email": "private@example.org"}],
        "cads:disabled_reason": None,
        "cads:sanity_check": {"status": "available", "timestamp": "2026-10-04T12:00:00Z"},
    }


def catalogue():
    return {
        "collections": [collection(), collection("s2s-forecasts")],
        "numberMatched": 2,
        "numberReturned": 2,
        "links": [{"rel": "self", "href": URL}],
    }


def response(data=None):
    return Response(200, json.dumps(data or catalogue()).encode(), {}, URL)


class EcmwfCatalogueTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=SLUG,
            adapter="stac",
            url=URL,
            config=deepcopy(CONFIG),
            enabled=True,
            activation_approved=True,
        )
        self.http = Mock()
        self.http.max_bytes = 5 * 1024 * 1024
        self.http.get.return_value = response()

    def registry(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        return RegistryEntry.objects.create(
            registry_id=203,
            release=release,
            integration_state="needs_connector",
            master={},
            technical={},
        )

    def test_public_metadata_preserves_raw_extent_dates_doi_without_child_fetch(self):
        entries, rejected, result, digest = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected, result.status), (2, 0, 200))
        self.assertEqual(len(digest), 64)
        self.http.get.assert_called_once_with(URL, {"Accept": "application/json"})
        self.assertEqual(self.http.max_bytes, 5 * 1024 * 1024)
        entry = entries[0]
        self.assertEqual(entry.key, hashlib.sha256(b"tigge-forecasts").hexdigest())
        self.assertEqual(entry.published, "2026-04-01T00:00:00Z")
        self.assertEqual(entry.metadata["extent_raw"]["spatial"]["bbox"], [[0, -90, 360, 90]])
        self.assertIn("2026-11-17", str(entry.metadata["extent_raw"]))
        self.assertEqual(entry.metadata["license_raw"], "other")
        for flag in ("resources_downloaded", "terms_accepted", "full_ecmwf_catalogue_verified"):
            self.assertIs(entry.metadata[flag], False)
        for omitted in ("file.grib", "private@", "Full source prose", "sanity_check"):
            self.assertNotIn(omitted, json.dumps(entry.metadata))

    def test_strict_schema_counts_no_partial_or_pagination(self):
        changes = (
            lambda d: d.update(numberMatched=3),
            lambda d: d.update(numberMatched=True),
            lambda d: d.update(numberReturned=1),
            lambda d: d.update(collections=[]),
            lambda d: d["links"].append({"rel": "next", "href": URL + "?offset=2"}),
            lambda d: d["links"][0].update(href=URL + "?q=other"),
            lambda d: d["collections"][1].update(id="tigge-forecasts"),
            lambda d: d["collections"][1].update(type="Feature"),
            lambda d: d["collections"][1].update(stac_version="1.0.0"),
            lambda d: d["collections"][1].update(license="CC-BY-4.0"),
            lambda d: d["collections"][1].update(title="<p></p>"),
            lambda d: d["collections"][1].update(keywords=[None]),
            lambda d: d["collections"][1].update(**{"sci:doi": "javascript:evil"}),
            lambda d: d["collections"][1]["links"][0].update(href=URL + "/foreign"),
        )
        for change in changes:
            with self.subTest(change=change):
                data = catalogue()
                change(data)
                with self.assertRaises(FetchError):
                    parse_catalogue(response(data))

    def test_extent_and_timestamps_fail_closed(self):
        boxes = ([True, -90, 360, 90], [0, -91, 360, 90], [360, -90, 0, 90], [0, -90, 361, 90])
        for box in boxes:
            data = catalogue()
            data["collections"][0]["extent"]["spatial"]["bbox"] = [box]
            with self.assertRaises(FetchError):
                parse_catalogue(response(data))
        for value in ("2026-02-30T00:00:00Z", "2026-10-04T00:00:00", "2026-10-04", True):
            data = catalogue()
            data["collections"][0]["updated"] = value
            with self.assertRaises(FetchError):
                parse_catalogue(response(data))
        data = catalogue()
        data["collections"][0]["extent"]["temporal"]["interval"] = [
            ["2026-10-05T00:00:00Z", "2026-10-04T00:00:00Z"]
        ]
        with self.assertRaises(FetchError):
            parse_catalogue(response(data))
        data["collections"][0]["extent"]["temporal"]["interval"] = [[None, None]]
        data["collections"][0].pop("published")
        parsed = parse_catalogue(response(data))[0]
        self.assertEqual(parsed.published, "")
        self.assertEqual(parsed.metadata["extent_raw"]["temporal"]["interval"], [[None, None]])

    def test_duplicate_json_nan_utf8_wrong_status_url_and_budget(self):
        raw = response().body
        for body in (
            b"{}\xff",
            b"[]",
            raw.replace(b'"numberMatched": 2', b'"numberMatched": 2, "numberMatched": 2'),
            raw.replace(b"360.0", b"NaN"),
            b" " * (MAX_BYTES + 1),
        ):
            with self.assertRaises(FetchError):
                parse_catalogue(Response(200, body, {}, URL))
        for result in (Response(404, raw, {}, URL), Response(200, raw, {}, URL + "/items")):
            with self.assertRaises(FetchError):
                parse_catalogue(result)

    def test_profile_change_no_http_and_cap_restored_after_failure(self):
        original = deepcopy(self.source.config)
        self.source.config["extra"] = True
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.http.get.assert_not_called()
        self.source.config = original
        self.http.get.side_effect = FetchError("network")
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.assertEqual(self.http.max_bytes, 5 * 1024 * 1024)

    def test_import_repeat_ignores_sanity_timestamp_and_prose_then_tracks_extent(self):
        with patch("ingestion.service.Client", return_value=self.http):
            first = run_batch(source_slug=SLUG, force=True)[0]
        changed = catalogue()
        changed["collections"][0]["description"] = "changed ignored prose"
        changed["collections"][0]["cads:sanity_check"]["timestamp"] = "2026-10-05T00:00:00Z"
        changed["collections"].reverse()
        self.http.get.return_value = response(changed)
        with patch("ingestion.service.Client", return_value=self.http):
            repeat = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual((first["created"], repeat["unchanged"], repeat["updated"]), (2, 2, 0))
        item = Item.objects.get(metadata__collection_id="tigge-forecasts")
        self.assertEqual(item.versions.count(), 1)
        self.assertIsNotNone(item.published_at)
        changed["collections"][1]["extent"]["temporal"]["interval"][0][1] = "2026-11-18T00:00:00Z"
        self.http.get.return_value = response(changed)
        with patch("ingestion.service.Client", return_value=self.http):
            update = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual(update["updated"], 1)
        self.assertEqual(item.versions.count(), 2)
        self.assertContains(self.client.get(f"/items/{item.pk}/"), "nie pobrane prognozy")
        self.assertContains(self.client.get("/?record_scope=catalogues&status=all"), item.title)
        self.assertNotContains(
            self.client.get("/?record_scope=publications&status=all"), item.title
        )

    def test_invalid_second_row_never_persists_partial_catalogue(self):
        data = catalogue()
        data["collections"][1]["title"] = None
        self.http.get.return_value = response(data)
        with patch("ingestion.service.Client", return_value=self.http):
            run = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(Item.objects.count(), 0)

    def test_configure_dry_apply_repeat_preserves_other_sources_and_active_state(self):
        self.source.delete()
        registry = self.registry()
        cds = Source.objects.create(slug="cds-existing", url="https://cds.climate.copernicus.eu/")
        before_cds = Source.objects.values().get(pk=cds.pk)
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command("configure_ecmwf", stdout=io.StringIO())
            self.assertEqual(Source.objects.count(), 1)
            call_command("configure_ecmwf", apply=True, stdout=io.StringIO())
            source = Source.objects.get(slug=SLUG)
            self.assertEqual((source.enabled, source.activation_approved), (False, False))
            Source.objects.filter(pk=source.pk).update(
                enabled=True,
                activation_approved=True,
                interval_minutes=720,
                last_audit={"status": "verified", "count": 2},
            )
            before = Source.objects.values().get(pk=source.pk)
            call_command("configure_ecmwf", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.values().get(pk=source.pk), before)
        self.assertEqual(Source.objects.values().get(pk=cds.pk), before_cds)
        self.assertEqual(set(source.registry_entries.values_list("registry_id", flat=True)), {203})
        registry.refresh_from_db()
        self.assertEqual(registry.integration_state, "needs_connector")

    def test_configure_refuses_missing_registry_changed_profile_or_other_linkage(self):
        with self.assertRaises(CommandError):
            call_command("configure_ecmwf", apply=True, stdout=io.StringIO())
        registry = self.registry()
        registry.sources.add(self.source)
        Source.objects.filter(pk=self.source.pk).update(config={"mode": "admin"})
        with self.assertRaises(CommandError):
            call_command("configure_ecmwf", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.get(pk=self.source.pk).config, {"mode": "admin"})
        self.source.delete()
        registry.sources.add(Source.objects.create(slug="admin-other"))
        with self.assertRaises(CommandError):
            call_command("configure_ecmwf", apply=True, stdout=io.StringIO())

    def test_redirect_refused_before_second_request_and_robots_disallow_respected(self):
        client = Client()
        raw = MagicMock()
        raw.code = 302
        raw.headers = {"Location": URL + "/tigge-forecasts/items"}
        raw.__enter__.return_value = raw
        client.opener = Mock()
        client.opener.open.return_value = raw
        with patch("ingestion.transport.validate_url"), patch("ingestion.transport.time.sleep"):
            with self.assertRaisesMessage(FetchError, "ECMWF collection API redirect rejected"):
                client._raw(URL)
        client.opener.open.assert_called_once()
        client = Client()
        with patch.object(
            client,
            "_request",
            return_value=Response(
                200, b"User-agent: *\nDisallow: /api/", {}, "https://ecds.ecmwf.int/robots.txt"
            ),
        ) as request:
            with self.assertRaises(FetchError):
                client.get(URL)
        request.assert_called_once()
