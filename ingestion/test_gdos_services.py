import io
from html import escape
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.gdos_services import CONFIG, SLUG, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import FetchError, Response

WMS = "https://sdi.gdos.gov.pl/wms"
WFS = "https://sdi.gdos.gov.pl/wfs"
FILE = (
    WFS + "?SERVICE=WFS&VERSION=1.0.0&REQUEST=GetFeature&TYPENAME=GDOS:ParkiNarodowe"
    "&SRSNAME=EPSG:2180&outputFormat=shape-zip&format_options=charset:windows-1250"
)


def body(links=(WMS, WFS, FILE), heading="Dostęp do danych geoprzestrzennych"):
    return (
        f"<h2>{heading}</h2><div class='editor-content'>"
        + "".join(
            f'<a href="{escape(link, quote=True)}">Link {i}</a>' for i, link in enumerate(links)
        )
        + "</div>"
    ).encode()


class GdosServicesTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=SLUG,
            adapter="web",
            url=URL,
            config=CONFIG.copy(),
            enabled=True,
            activation_approved=True,
        )
        self.http = Mock()
        self.http.get.return_value = Response(200, body(), {}, URL)

    def test_directory_no_sdi_fetch_or_invented_dates(self):
        entries, rejected, response, digest = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected, response.status), (3, 0, 200))
        self.assertEqual(len(digest), 64)
        self.http.get.assert_called_once_with(URL, {})
        self.assertTrue(all(not e.published and not e.summary for e in entries))
        self.assertEqual(entries[2].metadata["feature_type"], "GDOS:ParkiNarodowe")
        self.assertEqual(entries[2].metadata["record_kind"], "service_directory")
        self.assertEqual(entries[2].metadata["catalogue_url"], URL)

    def test_regional_filter_and_external_links(self):
        regional = FILE.replace("ParkiNarodowe", "PomnikiPrzyrody") + (
            "&CQL_FILTER=kodinspire%20ilike%20%27PL.ZIPOP.1393.PP.02%25%27"
        )
        self.http.get.return_value.body = body((WMS, WFS, regional, "https://example.org/ignored"))
        entries, rejected, _, _ = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected), (3, 0))
        self.assertIn(".02%", entries[2].metadata["regional_filter"])
        self.assertEqual(entries[2].metadata["source_link_warning"], "")
        self.http.get.return_value.body = body((WMS, WFS, regional + "%"))
        entries, rejected, _, _ = fetch_entries(self.source, self.http)
        self.assertEqual((len(entries), rejected), (3, 0))
        self.assertEqual(entries[2].url, regional + "%")
        self.assertIn("dodatkowy znak %", entries[2].metadata["source_link_warning"])

    def test_warning_visible_in_all_public_views_without_fetching_link(self):
        self.http.get.return_value.body = body(
            (
                WMS,
                WFS,
                FILE.replace("ParkiNarodowe", "PomnikiPrzyrody")
                + "&CQL_FILTER=kodinspire%20ilike%20%27PL.ZIPOP.1393.PP.02%25%27%",
            )
        )
        entries, _, _, _ = fetch_entries(self.source, self.http)
        item = Item.objects.create(
            source=self.source,
            external_id="warning",
            title="Województwo",
            url=entries[2].url,
            metadata=entries[2].metadata,
        )
        with patch("ingestion.transport.Client.get") as get:
            for path in (
                "/?record_scope=catalogues",
                "/search/?record_scope=catalogues",
                f"/items/{item.pk}/",
            ):
                response = self.client.get(path)
                self.assertContains(response, "Odnośnik wymaga sprawdzenia")
                self.assertContains(response, "Adres zachowano bez korekty")
        get.assert_not_called()

    def test_schema_duplicates_identity_and_budget_fail_closed(self):
        cases = [
            body((WMS, WFS, FILE + "&broken")),
            body((WMS, WFS, FILE + "\n")),
            body((WMS,)),
            body((WMS, WFS, FILE, FILE)),
            body(heading="Other"),
            body() + b"<div class='editor-content'></div>",
            body((WMS, WFS, FILE.replace("https:", "http:"))),
            body((WMS, WFS, FILE.replace("GetFeature", "Transaction"))),
            body((WMS, WFS, FILE + "&SERVICE=WFS")),
            body((WMS, WFS, FILE + "#fragment")),
            body((WMS, WFS, FILE.replace("sdi.gdos.gov.pl", "user@sdi.gdos.gov.pl"))),
            body((WMS, WFS, FILE.replace("EPSG:2180", "EPSG:4326"))),
            body((WMS, WFS, FILE + "&CQL_FILTER=malicious")),
            body((WMS, WFS, *[FILE.replace("ParkiNarodowe", f"Type{i}") for i in range(79)])),
        ]
        for content in cases:
            with self.subTest(content=content[:60]):
                self.http.get.return_value = Response(200, content, {}, URL)
                with self.assertRaises(FetchError):
                    fetch_entries(self.source, self.http)
        self.http.get.return_value = Response(200, body(), {}, URL + "/other")
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.http)

    def test_repeat_import_and_title_update_keep_history(self):
        with patch("ingestion.service.Client", return_value=self.http):
            self.assertEqual(run_batch(SLUG, force=True)[0]["created"], 3)
            self.assertEqual(run_batch(SLUG, force=True)[0]["unchanged"], 3)
            self.http.get.return_value.body = body().replace(b"Link 2", b"Changed label")
            self.assertEqual(run_batch(SLUG, force=True)[0]["updated"], 1)
        self.assertEqual(Item.objects.count(), 3)
        item = Item.objects.get(url=FILE)
        self.assertEqual(item.revision, 2)
        self.assertIsNone(item.published_at)
        self.assertEqual(item.versions.count(), 2)

    def test_configure_dry_apply_preserves_workbook_gap_audit_and_admin(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="test", sha256="8" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=16,
            release=release,
            priority="P0",
            master={"original": 16},
            technical={},
            integration_state="needs_connector",
        )
        with patch("ingestion.transport.Client.get") as get:
            call_command("configure_gdos_services", stdout=io.StringIO())
            self.assertFalse(Source.objects.exists())
            call_command("configure_gdos_services", apply=True, stdout=io.StringIO())
            source = Source.objects.get(slug=SLUG)
            self.assertFalse(source.enabled or source.activation_approved)
            Source.objects.filter(pk=source.pk).update(
                enabled=True, activation_approved=True, last_audit={"status": "verified"}
            )
            call_command("configure_gdos_services", apply=True, stdout=io.StringIO())
            source.refresh_from_db()
            self.assertTrue(source.enabled and source.activation_approved)
            self.assertEqual(source.last_audit, {"status": "verified"})
            entry.refresh_from_db()
            self.assertEqual(entry.integration_state, "needs_connector")
            self.assertEqual(entry.master, {"original": 16})
            source.config = {"listing": "custom"}
            source.save()
            with self.assertRaises(CommandError):
                call_command("configure_gdos_services", apply=True, stdout=io.StringIO())
        get.assert_not_called()
