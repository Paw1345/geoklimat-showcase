import io
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.normalization import normalized_fields
from ingestion.transport import FetchError, Response
from ingestion.wios_lodz import HOME, profile, publication_day


def fixture(code="news", count=1, **changes):
    p = profile(code)
    cards = []
    for i in range(count):
        onclick = changes.get("onclick", f"location.href='Notice_{i},{p['category']},{500 + i}';")
        day = changes.get("date", "1 październik 2026")
        cards.append(
            '<div class="news_spacer"><div class="m_news_right">'
            f"<h3>{escape(changes.get('title', 'Official notice'))}</h3>"
            f'<div class="col_gray">{escape(day)}</div>'
            '<div class="mar10">fixture-contact-not-to-store</div>'
            f'<input class="more_m" onclick="{escape(onclick, {'"': "&quot;"})}"/>'
            "</div></div>"
        )
    return (
        f"<html><head><title>{escape(changes.get('page_title', p['title'] + ' - WIOŚ w Łodzi'))}"
        "</title></head><body>"
        f'<div id="text_title"><h2>{escape(changes.get("heading", p["title"]))}</h2></div>'
        '<nav><h3>Not a record</h3></nav><div id="text_content">'
        + "".join(cards)
        + "</div><footer>fixture-private-footer</footer></body></html>"
    ).encode()


class WiosLodzParserTests(SimpleTestCase):
    def perform(self, code="news", body=None, **changes):
        p = profile(code)
        source = Source(adapter="web", url=p["url"], config=p["config"], language="pl")
        return parse(source, Response(200, body or fixture(code, **changes), {}, p["url"]))

    def test_both_windows_metadata_and_day_precision(self):
        for code in ["news", "notices"]:
            rows, rejected = self.perform(code)
            self.assertEqual((len(rows), rejected), (1, 0))
            row = rows[0]
            self.assertEqual(row.summary, "")
            self.assertEqual(row.updated, "")
            self.assertEqual(row.published, "2026-10-01")
            self.assertEqual(row.metadata["source_publication_date_text"], "1 październik 2026")
            self.assertNotIn("authors", row.metadata)
            self.assertFalse(row.metadata["full_document_downloaded"])
            item = Item(
                source=Source(language="pl"),
                title=row.title,
                url=row.url,
                metadata=row.metadata,
            )
            self.assertEqual(normalized_fields(item)["date_precision"], "day")

    def test_dates_are_strict_valid_days_not_event_deadlines(self):
        self.assertEqual(publication_day("29 wrzesień 2026"), "2026-09-29")
        self.assertEqual(publication_day("29 luty 2024"), "2024-02-29")
        for raw in [
            "29 luty 2026",
            "31 kwiecień 2026",
            "1 unknown 2026",
            "1 październik 2026 12:00",
            "termin: 1 październik 2026",
            "",
        ]:
            with self.subTest(raw=raw), self.assertRaises(FetchError):
                publication_day(raw)

    def test_onclick_is_pinned_data_not_executable_code_or_cross_category(self):
        for onclick in [
            "location.href='https://example.com/Notice,46,1';",
            "location.href='../Notice,46,1';",
            "location.href='Notice,47,1';",
            "location.href='Notice,46,0';",
            "location.href='Notice,46,1?q=1';",
            "location.href='Notice,46,1';alert(1);",
            "window.open('Notice,46,1');",
            "",
        ]:
            with self.subTest(onclick=onclick), self.assertRaises(FetchError):
                self.perform(onclick=onclick)

    def test_identity_fields_duplicates_and_budget_fail_closed(self):
        for changes in [
            {"page_title": "Other institution"},
            {"heading": "Other category"},
            {"title": ""},
            {"date": "31 luty 2026"},
            {"count": 0},
            {"count": 51},
            {"count": 2, "onclick": "location.href='Notice,46,1';"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)
        with self.assertRaises(FetchError):
            self.perform(body=fixture().replace(b"<h3>", b"<h3>Ambiguous</h3><h3>"))
        body = fixture(count=2).replace(
            b"1 pa\xc5\xbadziernik 2026", b"2 pa\xc5\xbadziernik 2026", 1
        )
        # Put an older date first; a later record must not sort ahead of it.
        body = body.replace(b"2 pa\xc5\xbadziernik 2026", b"30 wrzesie\xc5\x84 2026", 1)
        with self.assertRaises(FetchError):
            self.perform(body=body)

    def test_redirect_configuration_and_ambiguous_container(self):
        p = profile("news")
        source = Source(adapter="web", url=p["url"], config=p["config"])
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, HOME))
        source.config = {**p["config"], "extra": True}
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, p["url"]))
        with self.assertRaises(FetchError):
            self.perform(
                body=fixture().replace(
                    b'<div id="text_content">',
                    b'<div id="text_content"></div><div id="text_content">',
                )
            )


class WiosLodzSetupTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="registry.xlsx", sha256="c" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=86,
            release=release,
            organization="WIOŚ Łódź",
            name="News",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-086",
            name="WIOŚ Łódź",
            adapter="web",
            url=HOME,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=120,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            last_audit={"status": "unverified"},
            etag="old",
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_wios_lodz.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_wios_lodz", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_identity_and_approval(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.assertEqual(Source.objects.count(), 1)
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, profile("news")["url"])
            self.assertEqual(self.source.last_audit, {})
            self.assertEqual(self.source.etag, "")
            self.assertEqual(Source.objects.count(), 2)
            self.assertFalse(Source.objects.filter(activation_approved=True).exists())
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})
            self.entry.refresh_from_db()
            self.assertEqual(self.entry.integration_state, "configured")

    def test_admin_second_profile_blocks_first_replacement_atomically(self):
        source = Source.objects.create(
            slug="registry-086-notices",
            name="Admin",
            adapter="web",
            url=HOME,
            config={"admin": True},
        )
        self.entry.sources.add(source)
        with self.assertRaises(CommandError):
            self.command(True)
        self.source.refresh_from_db()
        self.assertEqual(self.source.url, HOME)
        self.assertEqual(self.source.last_audit, {"status": "unverified"})

    def test_busy_history_and_foreign_linkage_are_preserved(self):
        with self.assertRaises(CommandError):
            self.command(True, acquired=False)
        self.source.enabled = True
        self.source.save()
        with self.assertRaises(CommandError):
            self.command(True)
        self.source.enabled = False
        self.source.save()
        self.entry.sources.add(Source.objects.create(slug="admin", name="Admin", url=HOME))
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertFalse(Source.objects.filter(slug="registry-086-notices").exists())
