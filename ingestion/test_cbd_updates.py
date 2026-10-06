import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.cbd_updates import CONFIG, GROUPS, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def card(group, identifier="", title="", day=None):
    default_days = {"secretariat": "10.09.2026", "notification": "30.09.2026"}
    day = default_days.get(group, "") if day is None else day
    if group == "secretariat":
        path = URL + "doc/press/2026/pr-2026-09-10-kunming-en.pdf"
        title = title or "Press announcement"
    elif group == "notification":
        identifier = identifier or "2026-078"
        path = "/notifications/" + identifier
        title = title or "Notification"
    elif group == "event":
        path = URL + "meetings/" + (identifier or "006763")
        title = title or "Webinar 2026"
    else:
        identifier = identifier or "SBI-07"
        path = URL + "meetings/" + identifier
        title = title or "Recommendation 7/4 - Subject"
    labels = f'<span class="post-square"></span><span>{day}</span>'
    if identifier:
        labels += (
            f'<span class="post-slashes">//</span><span class="post-title">{identifier}</span>'
        )
    return (
        f'<div class="post-wrapper grid-sizer {group}">'
        f'<a class="post-header" href="{path}">{labels}</a>'
        f'<a class="post-content" href="{path}">{title}</a></div>'
    )


def page(changes=None):
    changes = changes or {}
    blocks = "".join(
        f'<div class="limited-width-content {g}">{changes.get(g, card(g))}</div>' for g in GROUPS
    )
    return (
        "<html><head><title>Home | Convention on Biological Diversity</title></head>"
        '<body><h2>Updates</h2><div class="block-cbd-utility block-filters-block">'
        '<div class="view-filtered-items">' + blocks + "</div></div></body></html>"
    ).encode()


class CbdParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_four_kinds_dates_unknown_and_no_document_download(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (4, 0))
        self.assertEqual([e.published for e in entries], ["2026-09-10", "2026-09-30", "", ""])
        for e in entries:
            self.assertEqual((e.updated, e.summary), ("", ""))
            self.assertFalse(e.metadata["full_document_downloaded"])
            self.assertFalse(e.metadata["current_legal_status_verified"])
            self.assertFalse(e.metadata["funding_call_confirmed"])
            self.assertNotIn("authors", e.metadata)

    def test_same_meeting_different_recommendations_are_separate(self):
        rows = card("recommendation") + card("recommendation", title="Recommendation 7/5 - Other")
        entries = self.perform(page({"recommendation": rows}))[0]
        self.assertEqual(entries[-1].url, entries[-2].url)
        self.assertNotEqual(entries[-1].key, entries[-2].key)
        changed = self.perform(
            page(
                {
                    "recommendation": card(
                        "recommendation", title="Recommendation 7/4 - Changed subject"
                    )
                }
            )
        )[0][-1]
        self.assertEqual(changed.key, entries[-2].key)
        self.assertNotEqual(changed.digest(), entries[-2].digest())

    def test_decision_branches_and_named_event_ids(self):
        rows = card(
            "recommendation", identifier="NP-MOP-03", title="Decision 3/1 A and B - Assessment"
        ) + card("recommendation", identifier="NP-MOP-01", title="Decision NP-1/1 - Procedure")
        entries = self.perform(
            page(
                {
                    "event": card("event", identifier="CP-BCH-RA-WS-2026-01"),
                    "recommendation": rows,
                }
            )
        )[0]
        self.assertEqual(entries[2].metadata["source_record_id"], "CP-BCH-RA-WS-2026-01")
        self.assertEqual(entries[3].metadata["source_citation"], "Decision 3/1 A and B")
        self.assertEqual(entries[4].metadata["source_citation"], "Decision NP-1/1")

    def test_visible_date_not_pdf_filename_date_and_position_excluded(self):
        rows = card("secretariat").replace("pr-2026-09-10", "pr-2026-09-09")
        e = self.perform(page({"secretariat": rows}))[0][0]
        self.assertEqual(e.published, "2026-09-10")
        first = self.perform()[0][-1]
        rows = card("recommendation", title="Recommendation 7/5 - Other") + card("recommendation")
        moved = self.perform(page({"recommendation": rows}))[0][-1]
        self.assertEqual(moved.digest(), first.digest())

    def test_page_schema_encoding_missing_tabs_fail_closed(self):
        for old, new in [
            ("Home | Convention on Biological Diversity", "Challenge"),
            ("<h2>Updates</h2>", "<h2>Other</h2>"),
            ('class="view-filtered-items"', 'class="other"'),
            ('class="block-cbd-utility block-filters-block"', 'class="other"'),
            ('class="post-header"', 'class="other"'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page({"event": ""})]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_dates_identifiers_link_kinds_and_unknown_children_fail_closed(self):
        cases = [
            {"secretariat": card("secretariat", day="31.02.2026")},
            {"secretariat": card("secretariat", day="10.09.2025")},
            {"notification": card("notification", identifier="2025-078")},
            {"notification": card("notification", day="")},
            {"event": card("event", day="01.10.2026")},
            {"recommendation": card("recommendation", title="Unnumbered recommendation")},
            {"recommendation": card("recommendation", identifier="SBI-7")},
            {"event": card("event") + "<div>Unknown record</div>"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(page(changes))
        for old, new in [
            ("https://www.cbd.int/doc/", "https://example.com/doc/"),
            ("pr-2026", "sp-2026"),
            (".pdf", ".pdf?download=1"),
            (
                '<a class="post-content" href="/notifications/2026-078"',
                '<a class="post-content" href="/notifications/2026-079"',
            ),
            ('<span class="post-title">SBI-07</span>', '<span class="post-title">SBI-08</span>'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))

    def test_duplicates_and_budgets_fail_closed(self):
        for body in [
            page({"event": card("event") * 2}),
            page({"event": "".join(card("event", identifier=f"EV-{n:03}") for n in range(101))}),
            page(
                {
                    "secretariat": "".join(
                        card("secretariat").replace("kunming", f"doc-{n}") for n in range(51)
                    ),
                    "notification": "".join(
                        card("notification", identifier=f"2026-{n:03}") for n in range(51)
                    ),
                    "event": "".join(card("event", identifier=f"EV-{n:03}") for n in range(51)),
                    "recommendation": "".join(
                        card("recommendation", title=f"Recommendation 7/{n} - Subject")
                        for n in range(1, 52)
                    ),
                }
            ),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_profile_status_and_response_url_guards(self):
        for config, status, url in [
            ({**CONFIG, "admin": True}, 200, URL),
            (CONFIG, 304, URL),
            (CONFIG, 200, "https://www.cbd.int/notifications"),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=URL, config=config), Response(status, page(), {}, url)
                )


class CbdConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=218,
            release=release,
            organization="CBD",
            name="Updates",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-218",
            name="Updates",
            adapter="web",
            url=URL,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=720,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            etag="old",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_cbd_updates.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_cbd_updates", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_no_http_preserves_identity_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.config, {})
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual((self.source.pk, self.source.config), (pk, CONFIG))
            self.assertEqual(self.source.interval_minutes, 720)
            self.assertEqual(self.source.last_audit, {})
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})

    def test_busy_admin_history_and_linkage_guards(self):
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
        self.entry.sources.add(Source.objects.create(slug="admin", name="Admin", url=URL))
        with self.assertRaises(CommandError):
            self.command(True)

    def test_existing_item_history_is_preserved(self):
        Item.objects.create(source=self.source, external_id="history", title="History", url=URL)
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(self.source.items.count(), 1)

    def test_ui_discloses_unknown_dates_meeting_link_and_escapes(self):
        entry = CbdParserTests().perform()[0][-1]
        entry.metadata["source_citation"] = "<script>alert(1)</script>"
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
            "Nie podano",
            "strony spotkania",
            "PDF-ów",
            "nie potwierdza",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
