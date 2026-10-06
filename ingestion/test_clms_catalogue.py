import io
import json
from copy import deepcopy
from unittest.mock import MagicMock, Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.clms_catalogue import (
    CONFIG,
    MAX_BYTES,
    ORIGIN,
    PATH,
    POLICY,
    SLUG,
    URL,
    page_url,
    parse_page,
)
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import Client, FetchError, Response


def collection(index):
    key = f"clms_{index:06d}"
    return {
        "id": key,
        "type": "Collection",
        "stac_version": "1.1.0",
        "title": f"CLMS collection {index}",
        "license": "proprietary",
        "description": "Full copyrighted description NOT stored",
        "sci:doi": "10.2909/test-doi",
        "keywords": ["CLMS", "Europe"],
        "extent": {
            "spatial": {"bbox": [[-32, 27, 46, 72]]},
            "temporal": {"interval": [["2016-08-01T00:00:00Z", None]]},
        },
        "summaries": {"gsd": [60.0], "proj:code": ["EPSG:3035"], "product:type": ["snow"]},
        "links": [
            {"rel": "self", "href": ORIGIN + PATH + "/" + key},
            {"rel": "license", "href": POLICY},
            {"rel": "items", "href": "https://example.org/items"},
        ],
        "assets": {"data": {"href": "https://example.org/large.tif"}},
        "contacts": [{"emails": [{"value": "private@example.org"}]}],
    }


def page(total=2, offset=0):
    count = min(100, total - offset)
    links = [{"rel": "self", "href": page_url(offset)}]
    if offset + count < total:
        links.append({"rel": "next", "method": "GET", "href": page_url(offset + count)})
    if offset:
        links.append({"rel": "previous", "href": page_url(offset - 100)})
    return {
        "numberMatched": total,
        "numberReturned": count,
        "collections": [collection(i + 1) for i in range(offset, offset + count)],
        "links": links,
    }


def response(data, offset=0, status=200):
    return Response(status, json.dumps(data).encode(), {}, page_url(offset))


class ClmsCatalogueTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=SLUG,
            adapter="stac",
            url=URL,
            config=CONFIG,
            enabled=True,
            activation_approved=True,
        )
        self.http = Mock()
        self.http.max_bytes = 5 * 1024 * 1024
        self.http.get.return_value = response(page())

    def test_bounded_metadata_no_assets_contacts_descriptions_or_publication_dates(self):
        entries, rejected, result, digest = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected, result.status), (2, 0, 200))
        self.assertEqual(len(digest), 64)
        self.http.get.assert_called_once_with(URL, {"Accept": "application/json"})
        self.assertEqual(self.http.max_bytes, 5 * 1024 * 1024)
        for entry in entries:
            self.assertEqual((entry.published, entry.updated, entry.summary), ("", "", ""))
            self.assertEqual(entry.metadata["record_kind"], "dataset_collection")
            self.assertEqual(entry.metadata["license_raw"], "proprietary")
            self.assertEqual(entry.metadata["license_url"], POLICY)
            self.assertEqual(entry.metadata["summaries_raw"]["gsd"], [60.0])
            self.assertEqual(entry.metadata["extent_raw"]["temporal"]["interval"][0][1], None)
            self.assertFalse(entry.metadata["resources_downloaded"])
            self.assertFalse(entry.metadata["full_clms_catalogue_verified"])
            stored = json.dumps(entry.metadata)
            for ignored in ("large.tif", "private@", "copyrighted"):
                self.assertNotIn(ignored, stored)

    def test_three_pages_verified_total_query_and_identity(self):
        self.http.get.side_effect = [response(page(221, off), off) for off in (0, 100, 200)]
        entries, rejected, _, _ = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected, len({e.key for e in entries})), (221, 0, 221))
        self.assertEqual(
            [c.args[0] for c in self.http.get.call_args_list],
            [page_url(off) for off in (0, 100, 200)],
        )

    def test_malicious_or_changed_next_query_refused_before_second_get(self):
        for href in (
            "https://example.org/secret",
            page_url(200),
            URL + "&offset=100&q=all",
            URL + "&offset=100&bbox=0,0,1,1",
            page_url(100).replace("https:", "http:"),
            page_url(100).replace("q=clms", "q=sentinel"),
        ):
            with self.subTest(href=href):
                data = page(101)
                data["links"][1]["href"] = href
                self.http.reset_mock()
                self.http.get.return_value = response(data)
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.http)
                self.http.get.assert_called_once()
                self.assertEqual(self.http.max_bytes, 5 * 1024 * 1024)

    def test_page_totals_links_and_status_fail_closed(self):
        mutations = (
            lambda d: d.update(numberMatched=True),
            lambda d: d.update(numberMatched=0),
            lambda d: d.update(numberMatched=1001),
            lambda d: d.update(numberReturned=True),
            lambda d: d.update(numberReturned=1),
            lambda d: d.update(collections=[]),
            lambda d: d.update(links=[]),
            lambda d: d["links"].append({"rel": "next", "href": page_url(100)}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = page()
                mutate(data)
                with self.assertRaises(FetchError):
                    parse_page(response(data), 0)
        for status in (304, 302, 403, 500):
            with self.assertRaises(FetchError):
                parse_page(response(page(), status=status), 0)

    def test_changed_total_duplicate_identity_and_reordering_discard_all(self):
        for second in (page(102, 100), page(101, 100), page(101, 100)):
            if second["numberMatched"] == 101:
                second["collections"][0] = collection(1)
            self.http.get.side_effect = [response(page(101)), response(second, 100)]
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)
        data = page()
        data["collections"].reverse()
        self.http.get.side_effect = None
        self.http.get.return_value = response(data)
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)

    def test_invalid_collection_schema_license_self_or_extent_rejected(self):
        mutations = (
            lambda r: r.update(id="sentinel-2-l2a"),
            lambda r: r.update(type="Feature"),
            lambda r: r.update(stac_version="1.0.0"),
            lambda r: r.update(title="<p></p>"),
            lambda r: r.update(license="CC-BY-4.0"),
            lambda r: r.update(keywords=[None]),
            lambda r: r.update(summaries=[]),
            lambda r: r["summaries"].update(gsd=[True]),
            lambda r: r["summaries"].update(gsd=[-1]),
            lambda r: r["links"][0].update(href=ORIGIN + PATH + "/other"),
            lambda r: r["links"][1].update(href="https://example.org/license"),
            lambda r: r["extent"]["spatial"].update(bbox=[[True, 27, 46, 72]]),
            lambda r: r["extent"]["spatial"].update(bbox=[[-181, 27, 46, 72]]),
            lambda r: r["extent"]["spatial"].update(bbox=[[50, 27, 46, 72]]),
            lambda r: r["extent"]["temporal"].update(interval=[["2026-02-30T00:00:00Z", None]]),
            lambda r: r["extent"]["temporal"].update(interval=[["2026-01-01T00:00:00", None]]),
            lambda r: r["extent"]["temporal"].update(
                interval=[["2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"]]
            ),
            lambda r: r.update(**{"sci:doi": "javascript:evil"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = page()
                mutate(data["collections"][0])
                with self.assertRaises(FetchError):
                    parse_page(response(data), 0)

    def test_duplicate_json_key_invalid_utf8_nan_or_page_budget_rejected(self):
        raw = response(page()).body
        for body in (
            b"{}\xff",
            b"[]",
            raw.replace(b'"numberMatched": 2', b'"numberMatched": 2, "numberMatched": 2'),
            raw.replace(b"60.0", b"NaN"),
            b" " * (MAX_BYTES + 1),
        ):
            with self.assertRaises(FetchError):
                parse_page(Response(200, body, {}, URL), 0)

    def test_transport_budget_restored_on_network_failure_and_total_budget(self):
        self.http.get.side_effect = FetchError("timeout")
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)
        self.assertEqual(self.http.max_bytes, 5 * 1024 * 1024)
        self.http.get.side_effect = None
        with patch("ingestion.clms_catalogue.MAX_TOTAL_BYTES", 1):
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)
        self.assertEqual(self.http.max_bytes, 5 * 1024 * 1024)

    def test_changed_profile_is_refused_without_http(self):
        for field, value in (("url", URL + "&bbox=0,0,1,1"), ("config", {**CONFIG, "extra": 1})):
            original = getattr(self.source, field)
            setattr(self.source, field, value)
            with self.assertRaises(FetchError):
                fetch_entries(self.source, self.http)
            self.http.get.assert_not_called()
            setattr(self.source, field, original)

    def registry(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        return RegistryEntry.objects.create(
            registry_id=192,
            release=release,
            organization="CLMS",
            name="Portal",
            master={},
            technical={},
            integration_state="needs_connector",
        )

    def test_configure_dry_apply_repeat_disabled_gap_and_existing_cdse_preserved(self):
        self.source.delete()
        row = self.registry()
        cdse = Source.objects.create(
            slug="registry-191",
            adapter="stac",
            url=ORIGIN + PATH,
            enabled=True,
            activation_approved=True,
            config={},
        )
        original = Source.objects.values().get(pk=cdse.pk)
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command("configure_clms", stdout=io.StringIO())
            self.assertEqual(Source.objects.count(), 1)
            call_command("configure_clms", apply=True, stdout=io.StringIO())
            source = Source.objects.get(slug=SLUG)
            self.assertFalse(source.enabled)
            self.assertFalse(source.activation_approved)
            self.assertEqual(
                set(source.registry_entries.values_list("registry_id", flat=True)), {192}
            )
            before = Source.objects.values().get(pk=source.pk)
            call_command("configure_clms", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.values().get(pk=source.pk), before)
        self.assertEqual(Source.objects.values().get(pk=cdse.pk), original)
        row.refresh_from_db()
        self.assertEqual(row.integration_state, "needs_connector")
        self.assertEqual((source.items.count(), source.runs.count()), (0, 0))
        from django.contrib.auth import get_user_model

        self.client.force_login(
            get_user_model().objects.create_user(username="operator", is_staff=True)
        )
        self.assertContains(self.client.get("/registry/192/"), "nie liczba unikatowych produktów")

    def test_configure_preserves_administrator_profile_linkage_and_missing_registry(self):
        with self.assertRaises(CommandError):
            call_command("configure_clms", apply=True, stdout=io.StringIO())
        self.registry().sources.add(self.source)
        Source.objects.filter(pk=self.source.pk).update(config={"mode": "admin"})
        with self.assertRaises(CommandError):
            call_command("configure_clms", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.get(pk=self.source.pk).config, {"mode": "admin"})

    def test_import_idempotency_and_history_scope_ui_and_catalogue_filter(self):
        with patch("ingestion.service.Client", return_value=self.http):
            first = run_batch(source_slug=SLUG, force=True)[0]
            second = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual((first["created"], second["created"], second["unchanged"]), (2, 0, 2))
        item = Item.objects.get(metadata__collection_id="clms_000001")
        self.assertEqual(item.versions.count(), 1)
        self.assertIsNone(item.published_at)
        self.assertContains(self.client.get(f"/items/{item.pk}/"), "nie pobrane dane")
        self.assertContains(self.client.get(f"/items/{item.pk}/"), "proprietary")
        self.assertContains(self.client.get("/?record_scope=catalogues&status=all"), item.title)
        self.assertNotContains(
            self.client.get("/?record_scope=publications&status=all"), item.title
        )
        changed = page()
        changed["collections"][0]["summaries"]["gsd"] = [20.0]
        self.http.get.return_value = response(changed)
        with patch("ingestion.service.Client", return_value=self.http):
            result = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual((result["created"], result["updated"]), (0, 1))
        item.refresh_from_db()
        self.assertEqual(item.versions.count(), 2)

    def test_later_invalid_page_never_persists_partial_catalogue(self):
        bad = deepcopy(page(101, 100))
        bad["collections"][0]["license"] = "unknown"
        self.http.get.side_effect = [response(page(101)), response(bad, 100)]
        with patch("ingestion.service.Client", return_value=self.http):
            result = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(Item.objects.count(), 0)

    def test_long_collection_ids_preserve_full_identity_without_database_overflow(self):
        data = page()
        raw_ids = ["clms_" + "a" * 65 + suffix for suffix in ("1", "2")]
        for row, key in zip(data["collections"], raw_ids, strict=True):
            row["id"] = key
            row["links"][0]["href"] = ORIGIN + PATH + "/" + key
        self.http.get.return_value = response(data)
        with patch("ingestion.service.Client", return_value=self.http):
            first = run_batch(source_slug=SLUG, force=True)[0]
            repeat = run_batch(source_slug=SLUG, force=True)[0]
        self.assertEqual((first["created"], repeat["unchanged"]), (2, 2))
        items = list(Item.objects.order_by("external_id"))
        self.assertEqual({item.metadata["collection_id"] for item in items}, set(raw_ids))
        self.assertEqual(len({item.external_id for item in items}), 2)
        self.assertTrue(all(len(item.external_id) == 64 for item in items))

    def test_redirect_cannot_fetch_child_items_before_scope_validation(self):
        for offset in (0, 100):
            client = Client()
            raw = MagicMock()
            raw.code = 302
            raw.headers = {"Location": ORIGIN + PATH + "/clms_000001/items"}
            raw.__enter__.return_value = raw
            client.opener = Mock()
            client.opener.open.return_value = raw
            with patch("ingestion.transport.validate_url"), patch("ingestion.transport.time.sleep"):
                with self.assertRaisesMessage(FetchError, "CLMS collection API redirect rejected"):
                    client._raw(page_url(offset))
            client.opener.open.assert_called_once()

    def test_robots_disallow_is_not_bypassed_by_alternative_profile(self):
        client = Client()
        with patch.object(
            client,
            "_request",
            return_value=Response(
                200, b"User-agent: *\nDisallow: /v1/collections", {}, ORIGIN + "/robots.txt"
            ),
        ) as request:
            with self.assertRaises(FetchError):
                client.get(URL)
        request.assert_called_once()
