import io
from datetime import timedelta
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.inspire_oeil import CONFIG, LEGACY_URL, REFERENCE, SLUG, TITLE, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.normalization import normalized_fields
from ingestion.transport import FetchError, Response


def fixture():
    return (
        "<html><head><title>Procedure File: 2025/0393(COD) | Legislative Observatory | "
        'European Parliament</title></head><body><main id="website-body">'
        '<h2 class="es_title-h1">2025/0393(COD)</h2>'
        f'<h2 class="es_title-h2">{escape(TITLE)}</h2>'
        '<div id="section1"><h2>Basic information</h2>'
        '<p class="text-danger">Awaiting committee decision</p></div>'
        '<div id="section5"><h2>Technical information</h2><table><tbody>'
        "<tr><th>Procedure reference</th><td>2025/0393(COD)</td></tr>"
        "<tr><th>Stage reached in procedure</th><td>Awaiting committee decision</td></tr>"
        "<tr><th>Legislative instrument</th><td>Directive</td></tr>"
        "<tr><th>Amendments and repeals</th><td>Amending Directive 2007/2 2004/0175(COD)</td></tr>"
        '</tbody></table></div><div id="section3"><h2>Key events</h2><table><thead><tr>'
        "<th>Date</th><th>Event</th><th>Reference</th><th>Summary</th></tr></thead><tbody>"
        "<tr><td>10/12/2025</td><td>Legislative proposal published</td>"
        '<td><a href="https://example.com/document.pdf">COM(2025)0985</a></td>'
        '<td><a href="https://example.com/person">fixture-summary-not-to-store</a></td></tr>'
        '</tbody></table></div><div id="section4"><h2>Forecasts</h2><table><thead><tr>'
        "<th>Date</th><th>Subject</th></tr></thead><tbody><tr><td>05/10/2026</td>"
        "<td>Indicative plenary sitting date, 1st reading</td></tr></tbody></table></div>"
        '<div id="section2"><h2>Key players</h2>fixture-person-not-to-store</div>'
        "<svg><title>pdf</title></svg></main><footer>fixture-contact-not-to-store</footer>"
        "</body></html>"
    ).encode()


class InspireOeilParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, body if body is not None else fixture(), {}, URL),
        )

    def test_source_dates_are_not_publication_or_effective_dates(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No child GET")):
            rows, rejected = self.perform()
        self.assertEqual((len(rows), rejected), (1, 0))
        r = rows[0]
        self.assertEqual((r.published, r.updated), ("", ""))
        self.assertEqual(r.metadata["procedure_reference"], REFERENCE)
        self.assertEqual(r.metadata["source_events"][0]["date"], "2025-12-10")
        self.assertEqual(r.metadata["source_events"][0]["date_kind"], "event")
        self.assertEqual(r.metadata["source_forecasts"][0]["date_kind"], "forecast")
        self.assertFalse(r.metadata["full_document_downloaded"])
        item = Item(source=Source(language="en"), title=r.title, url=r.url, metadata=r.metadata)
        self.assertEqual(normalized_fields(item)["date_precision"], "unknown")
        for excluded in ["fixture-person", "fixture-contact", "fixture-summary", "example.com"]:
            self.assertNotIn(excluded, str(r))
        self.assertNotIn("authors", r.metadata)

    def test_procedure_identity_status_fields_encoding_and_response_guards(self):
        replacements = [
            (b"2025/0393(COD)", b"2025/0394(COD)"),
            (b"European Parliament</title>", b"Other publisher</title>"),
            (b"2007/2 2004/0175(COD)", b"2008/2 2004/0175(COD)"),
            (b"<td>Awaiting committee decision</td>", b"<td>Completed</td>"),
            (
                b'<p class="text-danger">',
                b'<p class="text-danger">Other</p><p class="text-danger">',
            ),
            (b"<th>Reference</th>", b"<th>Other</th>"),
            (b"<h2>Forecasts</h2>", b"<h2>Documents</h2>"),
            (
                b'<main id="website-body">',
                b'<main id="website-body"></main><main id="website-body">',
            ),
        ]
        for old, new in replacements:
            with self.subTest(new=new), self.assertRaises(FetchError):
                self.perform(fixture().replace(old, new))
        with self.assertRaises(FetchError):
            self.perform(fixture() + b"\xff")
        source = Source(adapter="web", url=URL, config=CONFIG)
        for response in [
            Response(202, fixture(), {}, URL),
            Response(200, fixture(), {}, URL + "&section=OTHER"),
        ]:
            with self.assertRaises(FetchError):
                parse(source, response)
        source.config = {**CONFIG, "extra": True}
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, URL))

    def test_dates_duplicates_budgets_and_empty_forecast(self):
        for old, new in [
            (b"10/12/2025", b"31/02/2025"),
            (b"05/10/2026", b"05/10/26"),
            (b"Legislative proposal published", b""),
        ]:
            with self.subTest(new=new), self.assertRaises(FetchError):
                self.perform(fixture().replace(old, new))
        start = fixture().index(b"<tr><td>10/12/2025")
        end = fixture().index(b"</tr>", start) + len(b"</tr>")
        row = fixture()[start:end]
        for count in [0, 2, 101]:
            with self.subTest(count=count), self.assertRaises(FetchError):
                self.perform(fixture().replace(row, row * count))
        forecast = (
            b"<tr><td>05/10/2026</td><td>Indicative plenary sitting date, 1st reading</td></tr>"
        )
        self.assertEqual(
            self.perform(fixture().replace(forecast, b""))[0][0].metadata["source_forecasts"], []
        )

    def test_status_changes_keep_key_and_svg_titles_do_not_affect_identity(self):
        first = self.perform()[0][0]
        changed = self.perform(fixture().replace(b"Awaiting committee decision", b"First reading"))[
            0
        ][0]
        self.assertEqual(first.key, changed.key)
        self.assertNotEqual(first.digest(), changed.digest())
        changed_footer = self.perform(
            fixture().replace(b"fixture-person-not-to-store", b"Other person")
        )[0][0]
        self.assertEqual(first.digest(), changed_footer.digest())


class InspireOeilSetupTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="registry.xlsx", sha256="f" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=168,
            release=release,
            organization="EUR-Lex",
            name="Portal",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="linked",
            master={},
            technical={},
        )
        self.primary = Source.objects.create(
            slug="eurlex-inspire",
            name="EUR-Lex",
            organization="EUR-Lex",
            url=LEGACY_URL,
            adapter="watch",
            config={"content": "#MainContent"},
            enabled=True,
            activation_approved=True,
            interval_minutes=30,
            failures=16,
            last_success_at=timezone.now() - timedelta(days=2),
        )
        self.entry.sources.add(self.primary)
        self.item = Item.objects.create(
            source=self.primary,
            external_id="old",
            title="Historical snapshot",
            url=LEGACY_URL,
            content_hash="a" * 64,
        )

    def command(self, apply=False, pause=False, acquired=True):
        with patch("ingestion.management.commands.configure_inspire_oeil.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command(
                "configure_inspire_oeil",
                apply=apply,
                pause_blocked_primary=pause,
                stdout=io.StringIO(),
            )

    def successful_companion(self):
        self.command(True)
        companion = Source.objects.get(slug=SLUG)
        companion.enabled = companion.activation_approved = True
        companion.audited_at = companion.last_success_at = timezone.now()
        companion.last_audit = {"status": "verified", "count": 1, "rejected": 0}
        companion.save()
        Item.objects.create(
            source=companion,
            external_id="new",
            title=TITLE,
            url=URL,
            content_hash="b" * 64,
            metadata={"procedure_reference": REFERENCE},
        )
        self.primary.last_audit = {"status": "unverified", "error": "Cannot confirm robots policy"}
        self.primary.audited_at = timezone.now()
        self.primary.save()
        return companion

    def test_create_dry_repeat_keep_old_publisher_history_and_new_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.assertEqual(Source.objects.count(), 1)
            self.command(True)
            c = Source.objects.get(slug=SLUG)
            self.assertFalse(c.enabled)
            self.assertFalse(c.activation_approved)
            self.assertEqual(c.interval_minutes, 360)
            self.assertNotEqual(c.organization, self.primary.organization)
            c.last_audit = {"status": "verified"}
            c.save()
            self.command(True)
            c.refresh_from_db()
            self.assertEqual(c.last_audit, {"status": "verified"})
        self.primary.refresh_from_db()
        self.item.refresh_from_db()
        self.assertTrue(self.primary.enabled)
        self.assertEqual(self.primary.failures, 16)
        self.assertEqual(self.item.source_id, self.primary.pk)
        self.assertEqual(self.entry.integration_state, "linked")

    def test_pause_only_after_fresh_import_and_audits_changes_enabled_flag(self):
        with self.assertRaises(CommandError):
            self.command(True, pause=True)
        c = self.successful_companion()
        before = Source.objects.filter(pk=self.primary.pk).values().get()
        self.command(pause=True)
        self.primary.refresh_from_db()
        self.assertTrue(self.primary.enabled)
        self.command(True, pause=True)
        after = Source.objects.filter(pk=self.primary.pk).values().get()
        self.assertFalse(after.pop("enabled"))
        before.pop("enabled")
        self.assertEqual(before, after)
        c.refresh_from_db()
        self.assertTrue(c.enabled)
        self.assertEqual(Item.objects.filter(source=self.primary).count(), 1)

    def test_pause_guards_reject_stale_unsuccessful_or_wrong_procedure(self):
        c = self.successful_companion()
        for field, value in [
            ("audited_at", timezone.now() - timedelta(hours=1)),
            ("activation_approved", False),
            ("enabled", False),
            ("last_success_at", None),
        ]:
            original = getattr(c, field)
            setattr(c, field, value)
            c.save()
            with self.assertRaises(CommandError):
                self.command(True, pause=True)
            self.primary.refresh_from_db()
            self.assertTrue(self.primary.enabled)
            setattr(c, field, original)
            c.save()
        c.items.update(metadata={"procedure_reference": "other"})
        with self.assertRaises(CommandError):
            self.command(True, pause=True)

    def test_admin_busy_linkage_and_companion_config_preserved(self):
        with self.assertRaises(CommandError):
            self.command(True, acquired=False)
        self.primary.config = {"admin": True}
        self.primary.save()
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.count(), 1)
        self.primary.config = {"content": "#MainContent"}
        self.primary.save()
        self.command(True)
        Source.objects.filter(slug=SLUG).update(config={"admin": True})
        with self.assertRaises(CommandError):
            self.command(True)

    def test_item_view_distinguishes_forecasts_and_escapes_source_text(self):
        c = self.successful_companion()
        item = c.items.get()
        item.metadata = {
            "record_kind": "procedure_snapshot",
            "procedure_reference": REFERENCE,
            "procedure_status_text": "<script>unsafe</script>",
            "source_events": [
                {
                    "date": "2025-12-10",
                    "source_date_text": "10/12/2025",
                    "label": "Proposal",
                    "reference": "COM(2025)0985",
                }
            ],
            "source_forecasts": [
                {
                    "date": "2026-10-05",
                    "source_date_text": "05/10/2026",
                    "label": "Indicative sitting",
                }
            ],
        }
        item.save()
        response = self.client.get(f"/items/{item.pk}/")
        self.assertContains(response, "Terminy planowane — nie potwierdzone wydarzenia")
        self.assertContains(response, "&lt;script&gt;unsafe&lt;/script&gt;")
        self.assertNotContains(response, "<script>unsafe</script>")
        self.assertContains(response, "nie są datami publikacji ani wejścia prawa w życie")
