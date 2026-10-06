import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.ipcc_reports import CONFIG, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def card(
    slug="planned-report", title="Report 2027", period="December 2027", kind="Methodology Report"
):
    url = f"https://www.ipcc.ch/report/{slug}/"
    return (
        '<div class="post post-preview small type-methodology-report">'
        f'<div class="preview-text"><h5>{kind}</h5><h4><a href="{url}">{title}</a></h4>'
        f'<h6>{period}</h6></div><div class="preview-btns"><a href="{url}">'
        "Explore</a></div></div>"
    )


def page(cards=None):
    return (
        '<html><head><title>Reports — IPCC</title></head><body id="page-reports">'
        '<h1>Reports</h1><section id="reports-post_grid-1"><div id="post-grid-1">'
        '<div class="section-posts post-grid-posts"><div class="grid-sizer"></div>'
        + (card() if cards is None else cards)
        + "</div></div></section></body></html>"
    ).encode()


class IpccParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_month_is_not_publication_or_status(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (1, 0))
        entry = entries[0]
        self.assertEqual((entry.published, entry.updated, entry.summary), ("", "", ""))
        self.assertEqual(entry.metadata["source_report_month"], "2027-12")
        self.assertEqual(entry.metadata["source_report_period_precision"], "month")
        self.assertFalse(entry.metadata["publication_status_verified"])
        self.assertFalse(entry.metadata["full_document_downloaded"])
        self.assertNotIn("authors", entry.metadata)

    def test_past_report_keeps_unknown_date_and_title_year_separate(self):
        entry = self.perform(page(card(title="Report 2006", period="May 2019")))[0][0]
        self.assertEqual(entry.published, "")
        self.assertEqual(entry.metadata["source_report_month"], "2019-05")

    def test_identity_stable_on_title_but_changes_are_observed(self):
        original = self.perform()[0][0]
        changed = self.perform(page(card(title="Changed title")))[0][0]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())
        moved = self.perform(page(card(slug="other") + card()))[0][1]
        self.assertEqual(original.digest(), moved.digest())

    def test_page_shape_encoding_and_unknown_grid_child_fail_closed(self):
        for old, new in [
            ("Reports — IPCC", "Challenge"),
            ('id="page-reports"', 'id="other"'),
            ('id="reports-post_grid-1"', 'id="other"'),
            ('id="post-grid-1"', 'id="other"'),
            ("<h1>Reports</h1>", "<h1>Other</h1>"),
            ('<div class="grid-sizer"></div>', '<div class="grid-sizer">record</div>'),
            ('<div class="grid-sizer"></div>', '<div class="unknown"></div>'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page("")]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_links_periods_fields_duplicates_and_budget_fail_closed(self):
        cases = [
            card(period="December 32, 2027"),
            card(period="Winter 2027"),
            card(period="December 2027-2028"),
            card(kind="Unknown"),
            card(title=""),
            card() + card(),
            "".join(card(slug=f"report-{n}") for n in range(101)),
        ]
        for rows in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(FetchError):
                self.perform(page(rows))
        for old, new in [
            ("https://www.ipcc.ch/report/", "https://example.com/report/"),
            ("planned-report/", "planned-report/?download=1"),
            (">Explore<", ">Download<"),
            ("<h6>", "<h3>"),
            ("</h6>", "</h6><h6>May 2019</h6>"),
            ('class="preview-text"', 'class="other"'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))

    def test_profile_status_response_endpoint_guards(self):
        for config, status, url in [
            ({**CONFIG, "admin": True}, 200, URL),
            (CONFIG, 304, URL),
            (CONFIG, 200, "https://www.ipcc.ch/"),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=URL, config=config), Response(status, page(), {}, url)
                )


class IpccConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=215,
            release=release,
            organization="IPCC",
            name="Reports",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-215",
            name="Reports",
            adapter="web",
            url=URL,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=1440,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            etag="old",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_ipcc_reports.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_ipcc_reports", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_identity_admin_and_no_http(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.config, {})
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual((self.source.pk, self.source.config), (pk, CONFIG))
            self.assertEqual(self.source.interval_minutes, 1440)
            self.assertEqual(self.source.last_audit, {})
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})

    def test_busy_admin_and_history_guards(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        for field, value in [
            ("enabled", True),
            ("activation_approved", True),
            ("failures", 1),
            ("interval_minutes", 60),
            ("last_success_at", timezone.now()),
            ("config", {"admin": True}),
            ("scope", "Custom"),
        ]:
            old = getattr(self.source, field)
            setattr(self.source, field, value)
            self.source.save()
            with self.assertRaises(CommandError):
                self.command(True)
            setattr(self.source, field, old)
            self.source.save()
        Item.objects.create(source=self.source, external_id="history", title="History", url=URL)
        with self.assertRaises(CommandError):
            self.command(True)

    def test_extra_linkage_is_preserved(self):
        self.entry.sources.add(Source.objects.create(slug="admin", name="Admin", url=URL))
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.get(pk=self.source.pk).config, {})

    def test_ui_discloses_announcements_unknown_dates_and_escapes(self):
        entry = IpccParserTests().perform()[0][0]
        entry.metadata["source_report_type"] = "<script>alert(1)</script>"
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
        for value in [
            "zapowiedziane raporty",
            "nie potwierdza publikacji",
            "dziennej daty",
            "bez PDF",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
