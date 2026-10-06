import io
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.normalization import normalized_fields
from ingestion.transport import FetchError, Response
from ingestion.wios_bydgoszcz import CONFIG, LEGACY_HOME, URL


def fixture(count=1, **changes):
    rows = []
    for i in range(count):
        identity = changes.get("identity", str(100 + i))
        link = changes.get("link", f"https://wios.bydgoszcz.pl/aktualnosci/notice-{i}/")
        more = changes.get("more", link)
        day = changes.get("date", "10.08.2026")
        title = escape(changes.get("title", "14 sierpnia urząd nieczynny"))
        rows.append(
            f'<div class="post-list-item type-post status-publish post-{identity}" '
            f'id="post-{identity}"><div class="post-date">{escape(day)}</div>'
            f'<h2><a href="{escape(link)}">{title}'
            f'</a></h2><p>fixture-contact-not-to-store</p><a class="post-item-link" '
            f'href="{escape(more)}">Więcej</a></div>'
        )
    return (
        "<html><head><title>"
        + escape(
            changes.get(
                "page_title", "Wojewódzki Inspektorat Ochrony Środowiska w Bydgoszczy | Aktualności"
            )
        )
        + "</title></head><body><nav>fixture-nav</nav><main><h1>"
        + escape(changes.get("heading", "Aktualności"))
        + "</h1>"
        + "".join(rows)
        + "</main><footer>fixture-private-footer</footer></body></html>"
    ).encode()


class BydgoszczParserTests(SimpleTestCase):
    def perform(self, body=None, **changes):
        source = Source(adapter="web", url=URL, config=CONFIG)
        return parse(source, Response(200, body or fixture(**changes), {}, URL))

    def test_utf8_source_day_not_title_event_or_contact(self):
        rows, rejected = self.perform()
        self.assertEqual((len(rows), rejected), (1, 0))
        row = rows[0]
        self.assertEqual(row.title, "14 sierpnia urząd nieczynny")
        self.assertEqual(row.published, "2026-08-10")
        self.assertEqual(row.metadata["source_publication_date_text"], "10.08.2026")
        self.assertEqual(row.summary, "")
        self.assertEqual(row.updated, "")
        self.assertNotIn("authors", row.metadata)
        item = Item(
            source=Source(language="pl"), title=row.title, url=row.url, metadata=row.metadata
        )
        self.assertEqual(normalized_fields(item)["date_precision"], "day")
        self.assertFalse(row.metadata["full_document_downloaded"])

    def test_stable_source_id_survives_slug_and_title_change(self):
        original = self.perform()[0][0]
        changed = self.perform(
            title="New title", link="https://wios.bydgoszcz.pl/aktualnosci/changed-slug/"
        )[0][0]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())

    def test_host_scope_link_conflict_and_source_identity(self):
        for changes in [
            {"link": "https://example.com/aktualnosci/notice/"},
            {"link": "http://wios.bydgoszcz.pl/aktualnosci/notice/"},
            {"link": "https://wios.bydgoszcz.pl/notice/"},
            {"link": "https://wios.bydgoszcz.pl/aktualnosci/notice/?page=2"},
            {"link": "https://wios.bydgoszcz.pl/aktualnosci/notice/#a"},
            {"more": "https://wios.bydgoszcz.pl/aktualnosci/other/"},
            {"identity": "0"},
            {"heading": "Other section"},
            {"page_title": "Other institution"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)

    def test_missing_ambiguous_date_duplicates_budget_and_order(self):
        for changes in [
            {"date": "31.02.2026"},
            {"date": "10.08.2026 12:00"},
            {"title": ""},
            {"count": 0},
            {"count": 51},
            {"count": 2, "identity": "100"},
            {"count": 2, "link": "https://wios.bydgoszcz.pl/aktualnosci/notice/"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)
        with self.assertRaises(FetchError):
            self.perform(
                body=fixture().replace(
                    b'<div class="post-date">',
                    b'<div class="post-date">10.08.2026</div><div class="post-date">',
                )
            )
        with self.assertRaises(FetchError):
            self.perform(body=fixture(count=2).replace(b"10.08.2026", b"09.08.2026", 1))

    def test_response_config_status_encoding_and_container_fail_closed(self):
        source = Source(adapter="web", url=URL, config=CONFIG)
        for response in [
            Response(200, fixture(), {}, LEGACY_HOME),
            Response(404, fixture(), {}, URL),
            Response(200, fixture() + b"\xff", {}, URL),
            Response(200, fixture().replace(b"<main>", b"<main></main><main>"), {}, URL),
            Response(200, fixture().replace(b"status-publish", b"status-draft"), {}, URL),
        ]:
            with self.assertRaises(FetchError):
                parse(source, response)
        source.config = {**CONFIG, "extra": True}
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, URL))


class BydgoszczSetupTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="registry.xlsx", sha256="d" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=83,
            release=release,
            organization="WIOŚ Bydgoszcz",
            name="News",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-083",
            name="News",
            adapter="web",
            url=LEGACY_HOME,
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
        with patch("ingestion.management.commands.configure_wios_bydgoszcz.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_wios_bydgoszcz", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_source_identity_and_approval(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, LEGACY_HOME)
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, URL)
            self.assertEqual(self.source.last_audit, {})
            self.assertEqual(self.source.etag, "")
            self.assertFalse(self.source.activation_approved)
            self.assertEqual(Source.objects.count(), 1)
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})
            self.entry.refresh_from_db()
            self.assertEqual(self.entry.integration_state, "configured")

    def test_admin_config_or_history_never_overwritten(self):
        for field, value in [
            ("config", {"admin": True}),
            ("enabled", True),
            ("last_success_at", timezone.now()),
        ]:
            original = getattr(self.source, field)
            setattr(self.source, field, value)
            self.source.save()
            with self.assertRaises(CommandError):
                self.command(True)
            setattr(self.source, field, original)
            self.source.save()
        self.assertEqual(Source.objects.get(pk=self.source.pk).url, LEGACY_HOME)

    def test_busy_or_foreign_linkage_no_changes(self):
        with self.assertRaises(CommandError):
            self.command(True, acquired=False)
        self.entry.sources.add(Source.objects.create(slug="admin", name="Admin", url=URL))
        with self.assertRaises(CommandError):
            self.command(True)
        self.source.refresh_from_db()
        self.assertEqual(self.source.url, LEGACY_HOME)
