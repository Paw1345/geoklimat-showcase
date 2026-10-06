import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response
from ingestion.unep_news import CONFIG, GROUPS, KINDS, URL


def card(
    group, slug="record", day="02 Sep 2026", kind="Story", timestamp="", title="Climate story"
):
    content = GROUPS[group]
    label = f'<time datetime="{timestamp}">{day}</time>' if timestamp else day
    path = f"/news-and-stories/{KINDS.get(kind, 'unknown')}/{slug}"
    return (
        f'<div class="{group}"><div class="{content}">'
        f'<div class="{content}_meta">{label} | {kind}</div>'
        f'<a href="{path}" hreflang="en">{title}</a></div></div>'
    )


def page(changes=None):
    changes = changes or {}
    cards = "".join(
        changes.get(group, card(group, slug=f"record-{index}"))
        for index, group in enumerate(GROUPS)
    )
    return (
        "<html><head><title>News, Stories &amp; Speeches | UNEP - UN Environment Programme"
        '</title></head><body><div class="gwm_ts_body press_room">'
        '<div class="news_header"><h1>News, Stories &amp; Speeches</h1></div>'
        + cards
        + "</div></body></html>"
    ).encode()


class UnepParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_all_four_groups_metadata_only_day_precision(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (4, 0))
        for entry in entries:
            self.assertEqual(
                (entry.published, entry.updated, entry.summary), ("2026-09-02", "", "")
            )
            self.assertEqual(entry.metadata["source_date_precision"], "day")
            self.assertFalse(entry.metadata["media_downloaded"])
            self.assertFalse(entry.metadata["full_document_downloaded"])
            self.assertFalse(entry.metadata["funding_call_confirmed"])
            self.assertNotIn("authors", entry.metadata)

    def test_timestamp_matches_local_day_without_upgrading_precision(self):
        group = next(iter(GROUPS))
        row = card(group, timestamp="2026-09-02T00:08:24+03:00", kind="Speech")
        entry = self.perform(page({group: row}))[0][0]
        self.assertEqual(entry.published, "2026-09-02")
        self.assertEqual(entry.metadata["source_card_timestamp"], "2026-09-02T00:08:24+03:00")

    def test_literal_template_whitespace_single_digit_day_and_kind(self):
        group = next(iter(GROUPS))
        row = card(group, day="9 Dec 2025", kind="Video").replace(
            "9 Dec 2025 |", r"9 Dec 2025 \n \n |"
        )
        entry = self.perform(page({group: row}))[0][0]
        self.assertEqual(entry.published, "2025-12-09")
        self.assertEqual(entry.metadata["source_document_type"], "Video")
        entry = self.perform(page({group: card(group, kind="Statements")}))[0][0]
        self.assertEqual(entry.metadata["source_document_type"], "Statements")

    def test_identity_omits_title_and_section_placement(self):
        original = self.perform()[0][0]
        first, second = list(GROUPS)[:2]
        changed = self.perform(page({first: card(first, slug="record-0", title="Changed")}))[0][0]
        self.assertEqual(changed.key, original.key)
        self.assertNotEqual(changed.digest(), original.digest())
        moved = self.perform(
            page(
                {
                    first: card(first, slug="record-1"),
                    second: card(second, slug="record-0"),
                }
            )
        )[0][1]
        self.assertEqual(moved.digest(), original.digest())

    def test_page_containers_encoding_and_missing_group_fail_closed(self):
        for old, new in [
            ("UNEP - UN Environment Programme", "Challenge"),
            ('class="gwm_ts_body press_room"', 'class="other"'),
            ('class="news_header"', 'class="other"'),
            ('class="news_featured_item_text"', 'class="other"'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page({"news_featured_item": ""})]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_date_kind_timestamp_and_link_conflicts_fail_closed(self):
        group = next(iter(GROUPS))
        cases = [
            card(group, day="31 Feb 2026"),
            card(group, day="2026-09-02"),
            card(group, day="2 Foo 2026"),
            card(group, kind="Unknown"),
            card(group, timestamp="2026-09-03T14:08:24+03:00"),
            card(group, timestamp="2026-09-02"),
            card(group, timestamp="2026-09-02T99:00:00+03:00"),
        ]
        for row in cases:
            with self.subTest(row=row[:70]), self.assertRaises(FetchError):
                self.perform(page({group: row}))
        for old, new in [
            ("/news-and-stories/story/", "https://example.com/story/"),
            ("/news-and-stories/story/", "/news-and-stories/video/"),
            ('hreflang="en"', 'hreflang="fr"'),
            ('record-0"', 'record-0?download=1"'),
            ("</div><a href=", '</div><a href="/news-and-stories/story/extra">Extra</a><a href='),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))

    def test_duplicates_group_and_combined_budgets_fail_closed(self):
        group = next(iter(GROUPS))
        for body in [
            page({group: card(group, slug="record-1")}),
            page({group: "".join(card(group, slug=f"many-{n}") for n in range(101))}),
            page(
                {
                    g: "".join(card(g, slug=f"{i}-{n}") for n in range(51))
                    for i, g in enumerate(GROUPS)
                }
            ),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_profile_http_and_response_url_guards(self):
        for config, status, url in [
            ({**CONFIG, "admin": True}, 200, URL),
            (CONFIG, 304, URL),
            (CONFIG, 200, "https://www.unep.org/"),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=URL, config=config), Response(status, page(), {}, url)
                )


class UnepConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=208,
            release=release,
            organization="UNEP",
            name="News",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-208",
            name="News",
            adapter="web",
            url=URL,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=360,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            etag="old",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_unep_news.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_unep_news", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_no_http_preserve_identity_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.config, {})
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual((self.source.pk, self.source.config), (pk, CONFIG))
            self.assertEqual(self.source.interval_minutes, 360)
            self.assertEqual(self.source.last_audit, {})
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})

    def test_busy_admin_history_guards(self):
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

    def test_ui_discloses_metadata_scope_and_escapes(self):
        entry = UnepParserTests().perform()[0][0]
        entry.metadata["source_document_type"] = "<script>alert(1)</script>"
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
            "czterech sekcji",
            "filmów",
            "nie jest analizą",
            "precyzją dnia",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
