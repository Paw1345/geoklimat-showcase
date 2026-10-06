import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.imgw_news import CONFIG, LEGACY, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def row(
    identifier="26863",
    slug="climate-news",
    title="Climate news",
    published="Fri, 02 Oct 2026 05:36:17 +0000",
):
    return (
        f"<item><title>{title}</title><link>https://imgw.pl/{slug}/</link>"
        f'<guid isPermaLink="false">https://imgw.pl/?p={identifier}</guid>'
        f"<pubDate>{published}</pubDate><category>Aktualności</category>"
        "<description>Article excerpt not stored</description>"
        "<content:encoded><![CDATA[Full RSS article ignored]]></content:encoded></item>"
    )


def feed(rows=None):
    return (
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>IMGW-PIB</title><link>https://imgw.pl</link><language>pl-PL</language>"
        f'<atom:link href="{URL}" rel="self" type="application/rss+xml"/>'
        "<lastBuildDate>Fri, 02 Oct 2026 05:36:18 +0000</lastBuildDate>"
        + (row() if rows is None else rows)
        + "</channel></rss>"
    ).encode()


class ImgwParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="feed", url=URL, config=CONFIG),
            Response(200, feed() if body is None else body, {}, URL),
        )

    def test_publication_timestamp_not_synthetic_updated_or_article_body(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (1, 0))
        e = entries[0]
        self.assertEqual(e.published, "2026-10-02T05:36:17+00:00")
        self.assertEqual((e.updated, e.summary), ("", ""))
        self.assertEqual(e.metadata["source_categories"], ["Aktualności"])
        self.assertFalse(e.metadata["article_body_stored"])
        self.assertFalse(e.metadata["measurement_record"])
        self.assertFalse(e.metadata["weather_alert_confirmed"])
        self.assertNotIn("authors", e.metadata)
        self.assertNotIn("Full RSS article ignored", str(e.metadata))

    def test_stable_guid_on_title_slug_changes_and_position_excluded(self):
        original = self.perform()[0][0]
        changed = self.perform(feed(row(slug="new-slug", title="New title")))[0][0]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())
        moved = self.perform(feed(row(identifier="999", slug="other") + row()))[0][1]
        self.assertEqual(original.digest(), moved.digest())

    def test_channel_build_date_and_content_changes_do_not_fake_item_update(self):
        original = self.perform()[0][0]
        body = (
            feed()
            .replace(b"05:36:18", b"05:37:18")
            .replace(b"Full RSS article ignored", b"Changed full article still ignored")
        )
        self.assertEqual(original.digest(), self.perform(body)[0][0].digest())

    def test_channel_identity_encoding_xml_and_dtd_fail_closed(self):
        for old, new in [
            ("IMGW-PIB", "Other institution"),
            ("<link>https://imgw.pl</link>", "<link>https://example.com</link>"),
            ("pl-PL", "en-US"),
            ('version="2.0"', 'version="1.0"'),
            ('href="https://imgw.pl/feed/"', 'href="https://imgw.pl/comments/feed/"'),
            ('rel="self"', 'rel="alternate"'),
            ("<title>IMGW-PIB</title>", "<title>IMGW-PIB</title><title>IMGW-PIB</title>"),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(feed().replace(old.encode(), new.encode()))
        for body in [
            b"\xff",
            b"<html>Challenge</html>",
            feed(""),
            b'<!DOCTYPE rss [<!ENTITY x "test">]>' + feed(),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_dates_timezone_weekday_conflicts_and_bad_identity_fail_closed(self):
        for date in [
            "Thu, 02 Oct 2026 05:36:17 +0000",
            "Fri, 31 Feb 2026 05:36:17 +0000",
            "Fri, 02 Oct 2026 99:36:17 +0000",
            "Fri, 02 Oct 2026 05:36:17",
            "2026-10-02",
            "Fri, 02 Oct 2026 05:36:17 +9999",
        ]:
            with self.subTest(date=date), self.assertRaises(FetchError):
                self.perform(feed(row(published=date)))
        for old, new in [
            ('isPermaLink="false"', 'isPermaLink="true"'),
            ("?p=26863", "?p=0"),
            ("?p=26863", "?p=26863&amp;tracking=1"),
            ("https://imgw.pl/climate-news/", "https://example.com/climate-news/"),
            ("climate-news/", "climate-news/?download=1"),
            ("<category>Aktualności</category>", "<category></category>"),
            ("<pubDate>", "<otherDate>"),
            ("<title>Climate news</title>", "<title></title>"),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(feed().replace(old.encode(), new.encode()))

    def test_duplicates_categories_fields_and_budget_fail_closed(self):
        for body in [
            feed(row() + row()),
            feed(row(identifier="999") + row()),
            feed(
                row().replace(
                    "<category>Aktualności</category>", "<category>Aktualności</category>" * 2
                )
            ),
            feed(
                row().replace(
                    "</pubDate>", "</pubDate><pubDate>Fri, 02 Oct 2026 05:36:17 +0000</pubDate>"
                )
            ),
            feed("".join(row(identifier=str(n + 1), slug=f"record-{n}") for n in range(101))),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_profile_adapter_response_and_status_guards(self):
        for adapter, config, status, url in [
            ("feed", {**CONFIG, "admin": True}, 200, URL),
            ("web", CONFIG, 200, URL),
            ("feed", CONFIG, 304, URL),
            ("feed", CONFIG, 200, LEGACY),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter=adapter, url=URL, config=config),
                    Response(status, feed(), {}, url),
                )


class ImgwConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=32,
            release=release,
            organization="IMGW",
            name="News",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-032",
            name="News",
            adapter="web",
            url=LEGACY,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=60,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            etag="old",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_imgw_news.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_imgw_news", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_no_http_preserve_identity_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual((self.source.url, self.source.adapter), (LEGACY, "web"))
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual(
                (self.source.pk, self.source.url, self.source.adapter), (pk, URL, "feed")
            )
            self.assertEqual(self.source.config, CONFIG)
            self.assertEqual(self.source.interval_minutes, 60)
            self.assertEqual(self.source.last_audit, {})
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})

    def test_busy_admin_history_linkage_guards(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        for field, value in [
            ("enabled", True),
            ("activation_approved", True),
            ("failures", 1),
            ("interval_minutes", 30),
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

    def test_existing_history_is_preserved(self):
        Item.objects.create(source=self.source, external_id="history", title="History", url=LEGACY)
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(self.source.items.count(), 1)

    def test_ui_discloses_news_not_measurements_and_escapes(self):
        entry = ImgwParserTests().perform()[0][0]
        entry.metadata["source_categories"] = ["<script>alert(1)</script>"]
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
            "nie podaje osobnej daty aktualizacji",
            "nie jest pomiarem",
            "Treści artykułów",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
