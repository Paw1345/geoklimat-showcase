import hashlib
import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response
from ingestion.unep_news import CONFIG, KINDS, LEGACY_NOTE, LEGACY_URL, NOTE, URL


def card(
    slug="record",
    day="02 Sep 2026",
    kind="Story",
    timestamp="2026-09-02T00:08:24+03:00",
    title="Climate story",
    path=None,
):
    path = path or f"/en/news-and-stories/{KINDS.get(kind, 'unknown')}/{slug}"
    return (
        '<div class="views-row"><div class="node node--type-news node--view-mode-card">'
        f'<div class="card__tag">{kind}</div>'
        '<div class="field field--name-field-main-image"><img src="/s3/x.webp" alt="x"/></div>'
        '<div class="card__meta"><div class="field field--name-published-at">'
        f'<time datetime="{timestamp}" title="{day}" class="datetime">{day}</time></div></div>'
        '<div class="field field--name-node-title"><h3>'
        f'<a href="{path}" hreflang="en">{title} </a></h3></div>'
        '<div class="field field--name-field-summary">Summary text.</div>'
        "</div></div>"
    )


def page(cards=None):
    cards = cards if cards is not None else [card(slug=f"record-{n}") for n in range(3)]
    return (
        "<html><head><title>News, stories &amp; speeches | UNEP - UN Environment Programme"
        "</title></head><body>"
        '<div class="view view-promoted-news view-id-promoted_news view-display-id-news_page">'
        '<div class="view-content">' + "".join(cards) + "</div></div>"
        '<div class="view view-social-media"><div class="view-content">'
        '<div class="views-row"><a href="https://x.example/post">post</a></div>'
        "</div></div></body></html>"
    ).encode()


class UnepParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_cards_become_metadata_only_day_precision_entries(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (3, 0))
        first = entries[0]
        # The key omits "/en" so stored records keep their identity; safe_entry hashes it.
        self.assertEqual(
            first.key,
            hashlib.sha256(b"unep:/news-and-stories/story/record-0").hexdigest(),
        )
        self.assertEqual(first.title, "Climate story")
        self.assertEqual(first.url, "https://www.unep.org/en/news-and-stories/story/record-0")
        for entry in entries:
            self.assertEqual(
                (entry.published, entry.updated, entry.summary), ("2026-09-02", "", "")
            )
            self.assertEqual(entry.metadata["source_date_precision"], "day")
            self.assertEqual(entry.metadata["source_publication_date_text"], "02 Sep 2026")
            self.assertEqual(entry.metadata["source_card_timestamp"], "2026-09-02T00:08:24+03:00")
            self.assertFalse(entry.metadata["media_downloaded"])
            self.assertFalse(entry.metadata["full_document_downloaded"])
            self.assertFalse(entry.metadata["funding_call_confirmed"])
            self.assertNotIn("authors", entry.metadata)

    def test_kinds_including_opinion_and_technical_highlight(self):
        kinds = ["Story", "Speech", "Press release", "Opinion", "Technical highlight"]
        cards = [card(slug=f"k-{n}", kind=kind) for n, kind in enumerate(kinds)]
        entries = self.perform(page(cards))[0]
        self.assertEqual([e.metadata["source_document_type"] for e in entries], kinds)
        self.assertEqual(
            entries[4].url, "https://www.unep.org/en/news-and-stories/technical-highlight/k-4"
        )

    def test_single_digit_day_and_local_day_of_card_offset(self):
        row = card(day="9 Dec 2025", timestamp="2025-12-09T00:00:00+03:00")
        self.assertEqual(self.perform(page([row]))[0][0].published, "2025-12-09")
        # 21:30Z on the 21st is already the 22nd at +03:00; the label follows the offset.
        row = card(day="22 Oct 2026", timestamp="2026-10-22T00:30:00+03:00")
        self.assertEqual(self.perform(page([row]))[0][0].published, "2026-10-22")

    def test_identity_omits_title(self):
        original = self.perform()[0][0]
        changed = self.perform(
            page([card(slug="record-0", title="Changed"), card(slug="record-1")])
        )[0][0]
        self.assertEqual(changed.key, original.key)
        self.assertNotEqual(changed.digest(), original.digest())

    def test_page_container_encoding_and_empty_list_fail_closed(self):
        for old, new in [
            ("UNEP - UN Environment Programme", "Challenge"),
            ("view-promoted-news", "view-other"),
            ("view-display-id-news_page", "view-display-id-other"),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page([])]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_card_without_date_tag_title_or_with_two_links_fails_closed(self):
        row = card()
        cases = {
            "no time": row.replace("<time", "<span").replace("</time>", "</span>"),
            "no tag": row.replace("card__tag", "other"),
            "no title link": row.replace("<h3>", "<h4>").replace("</h3>", "</h4>"),
            "two links": row.replace(
                "</h3>", '</h3><a href="/en/news-and-stories/story/extra">Extra</a>'
            ),
            "image link": row.replace(
                "<img", '<a href="/en/news-and-stories/story/i"><img'
            ).replace('alt="x"/>', 'alt="x"/></a>'),
            "not a news node": row.replace("node--type-news", "node--type-page"),
        }
        for name, bad in cases.items():
            with self.subTest(name), self.assertRaises(FetchError):
                self.perform(page([bad]))
        # One bad card poisons the whole batch.
        with self.assertRaises(FetchError):
            self.perform(page([card(slug="good"), cases["no time"]]))

    def test_date_kind_timestamp_and_link_conflicts_fail_closed(self):
        cases = [
            card(day="31 Feb 2026", timestamp="2026-02-28T00:00:00+03:00"),
            card(day="2026-09-02"),
            card(day="2 Foo 2026"),
            card(kind="Unknown"),
            card(timestamp="2026-09-03T14:08:24+03:00"),
            card(timestamp="2026-09-02"),
            card(timestamp="2026-09-02T00:08:24"),
            card(timestamp="2026-09-02T99:00:00+03:00"),
            card(timestamp=""),
            card(path="https://example.com/en/news-and-stories/story/x"),
            card(path="https://www.unep.org/en/news-and-stories/story/x"),
            card(path="/en/news-and-stories/video/x"),
            card(path="/news-and-stories/story/x"),
            card(path="/en/news-and-stories/story/x?download=1"),
            card(title=""),
        ]
        for row in cases:
            with self.subTest(row=row[160:330]), self.assertRaises(FetchError):
                self.perform(page([row]))
        with self.assertRaises(FetchError):
            self.perform(page().replace(b'hreflang="en"', b'hreflang="fr"'))

    def test_duplicate_path_and_budget_fail_closed(self):
        for cards in [
            [card(slug="same"), card(slug="same", title="Other")],
            [card(slug=f"many-{n}") for n in range(101)],
        ]:
            with self.assertRaises(FetchError):
                self.perform(page(cards))
        full = self.perform(page([card(slug=f"ok-{n}") for n in range(100)]))
        self.assertEqual(len(full[0]), 100)

    def test_profile_http_and_response_url_guards(self):
        for config, status, url, source_url in [
            ({**CONFIG, "admin": True}, 200, URL, URL),
            (CONFIG, 304, URL, URL),
            (CONFIG, 200, "https://www.unep.org/", URL),
            (CONFIG, 200, LEGACY_URL, URL),
            (CONFIG, 200, URL, LEGACY_URL),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=source_url, config=config),
                    Response(status, page(), {}, url),
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

    def test_unconfigured_registry_address_moves_to_new_url(self):
        Source.objects.filter(pk=self.source.pk).update(url=LEGACY_URL)
        self.command(True)
        self.source.refresh_from_db()
        self.assertEqual((self.source.url, self.source.config), (URL, CONFIG))

    def test_legacy_address_is_moved_keeping_approval_and_history(self):
        Source.objects.filter(pk=self.source.pk).update(
            url=LEGACY_URL,
            config=CONFIG,
            scope=LEGACY_NOTE,
            enabled=True,
            activation_approved=True,
            failures=17,
            last_audit={"status": "verified"},
        )
        Item.objects.create(source=self.source, external_id="old", title="Old", url=LEGACY_URL)
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, LEGACY_URL)
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual(
                (self.source.url, self.source.config, self.source.scope), (URL, CONFIG, NOTE)
            )
            self.assertTrue(self.source.enabled and self.source.activation_approved)
            self.assertEqual((self.source.failures, self.source.last_audit), (17, {}))
            self.assertEqual(self.source.items.count(), 1)
            self.command(True)
        Source.objects.filter(pk=self.source.pk).update(url=LEGACY_URL, scope="Custom")
        with self.assertRaises(CommandError):
            self.command(True)

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
            "Metadane kart z listy",
            "filmów",
            "nie jest analizą",
            "precyzją dnia",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
