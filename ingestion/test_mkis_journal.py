import io
import json
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.mkis_journal import CONFIG, LEGACY, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def row(**changes):
    return {
        "pageId": "21535806",
        "pageUrl": "/web/klimat/poz-44-zarzadzenie",
        "Pozycja": "(Poz. 44) ",
        "Tytuł aktu": "(Poz. 44) Zarządzenie z dnia 13 września 2026 r.",
        "Rok": json.dumps([{"id": "2026", "value": "2026"}]),
        "Organ wydający": json.dumps(
            [{"id": "Minister Klimatu i Środowiska", "value": "Minister Klimatu i Środowiska"}]
        ),
        "Typ": "[]",
        "Skorowidz": "[]",
        "Data ogłoszenia": "14.09.2026",
        "Data wydania": "13.09.2026",
        "Data wejścia w życie": "15.09.2026",
        "Akty powiązane": "",
        "pageOrder": "12",
        **changes,
    }


class JournalParserTests(SimpleTestCase):
    def perform(self, rows=None, **changes):
        body = json.dumps(rows or [row(**changes)], ensure_ascii=False).encode()
        return parse(Source(adapter="web", url=URL, config=CONFIG), Response(200, body, {}, URL))

    def test_distinct_source_dates_and_metadata_only(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (1, 0))
        entry = entries[0]
        self.assertEqual(entry.published, "2026-09-14")
        self.assertEqual(entry.metadata["source_issue_date"], "2026-09-13")
        self.assertEqual(entry.metadata["source_effective_date"], "2026-09-15")
        self.assertEqual((entry.summary, entry.updated), ("", ""))
        self.assertFalse(entry.metadata["current_legal_status_verified"])
        self.assertFalse(entry.metadata["full_document_downloaded"])
        self.assertNotIn("authors", entry.metadata)

    def test_hyphen_dates_empty_effective_and_stable_id(self):
        entry = self.perform(**{"Data ogłoszenia": "14-09-2026", "Data wejścia w życie": ""})[0][0]
        self.assertEqual(entry.published, "2026-09-14")
        self.assertEqual(entry.metadata["source_effective_date"], "")
        changed = self.perform(**{"pageUrl": "/web/klimat/poz-44-new-slug"})[0][0]
        self.assertEqual(entry.key, changed.key)

    def test_invalid_dates_identity_dictionary_and_links_fail_closed(self):
        cases = [
            {"Data ogłoszenia": "31.02.2026"},
            {"Data ogłoszenia": "14.09-2026"},
            {"Data wydania": "15.09.2026"},
            {"Data wejścia w życie": "bad"},
            {"Rok": '[{"id":"2025","value":"2025"}]'},
            {"Organ wydający": "[]"},
            {"Typ": '[{"id":"a","value":"b"}]'},
            {"Skorowidz": "[1]"},
            {"pageId": ""},
            {"pageUrl": "https://example.com/web/klimat/poz-44-x"},
            {"pageUrl": "/web/klimat/poz-43-x"},
            {"pageUrl": "/web/klimat/poz-44-x?a=1"},
            {"Tytuł aktu": "Unrelated article"},
            {"Akty powiązane": 1},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)

    def test_duplicates_missing_fields_endpoint_and_budgets(self):
        for rows in [[row(), row()], [{}], [row()] * 501]:
            with self.subTest(rows=len(rows)), self.assertRaises(FetchError):
                self.perform(rows)
        source = Source(adapter="web", url=URL, config=CONFIG)
        for body, url in [(b"[]", URL), (b"\xff", URL), (b"{}", URL), (b"[]", LEGACY)]:
            with self.assertRaises(FetchError):
                parse(source, Response(200, body, {}, url))
        source.config = {**CONFIG, "unknown": True}
        with self.assertRaises(FetchError):
            parse(source, Response(200, json.dumps([row()]).encode(), {}, URL))

    def test_page_order_not_record_change_but_effective_date_is(self):
        entry = self.perform()[0][0]
        changed = self.perform(pageOrder="99")[0][0]
        self.assertEqual(entry.digest(), changed.digest())
        changed = self.perform(**{"Data wejścia w życie": "16.09.2026"})[0][0]
        self.assertNotEqual(entry.digest(), changed.digest())


class JournalConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=4,
            release=release,
            organization="MKiŚ",
            name="Journal",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-004",
            name="Journal",
            adapter="web",
            url=LEGACY,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=120,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            etag="old",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_mkis_journal.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_mkis_journal", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_identity_admin_fields_and_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, LEGACY)
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual((self.source.pk, self.source.url), (pk, URL))
            self.assertEqual(self.source.last_audit, {})
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})
            self.assertEqual(Source.objects.count(), 1)

    def test_busy_admin_and_linkage_guards(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        for field, value in [
            ("config", {"admin": True}),
            ("enabled", True),
            ("last_success_at", timezone.now()),
            ("scope", "Custom scope"),
        ]:
            original = getattr(self.source, field)
            setattr(self.source, field, value)
            self.source.save()
            with self.assertRaises(CommandError):
                self.command(True)
            setattr(self.source, field, original)
            self.source.save()
        self.entry.sources.add(Source.objects.create(slug="admin", name="Admin", url=URL))
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.get(pk=self.source.pk).url, LEGACY)

    def test_item_view_explains_dates_unknown_effective_and_escapes(self):
        entry = JournalParserTests().perform(**{"Data wejścia w życie": ""})[0][0]
        entry.metadata["source_issue_date_text"] = "<script>alert(1)</script>"
        item = Item.objects.create(
            source=self.source,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            metadata=entry.metadata,
            content_hash=entry.digest(),
        )
        response = self.client.get(f"/items/{item.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nie podano")
        self.assertContains(response, "Data wydania aktu")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
