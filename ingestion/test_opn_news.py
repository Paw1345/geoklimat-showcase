import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.opn_news import CONFIG, LEGACY, URL
from ingestion.transport import FetchError, Response


def card(slug="climate-2027", day="2026-09-30", label="30 września 2026"):
    return (
        f'<a class="article-tile" href="/aktualnosci/{slug}">'
        '<h3 class="article-tile__title">Klimat 2027</h3>'
        '<div class="article-tile__excerpt">Skrót źródłowy</div>'
        f'<time class="article-tile__publication-date" datetime="{day}">{label}</time>'
        '<img src="/private.jpg"></a>'
    )


def page(cards=None):
    return (
        '<html><main id="main-content"><h1>Aktualności</h1>'
        '<div class="articles-grid js-pagination-result-container-news" '
        'data-pagination-url="/pl/front-api/news">'
        + (card() if cards is None else cards)
        + "</div><aside>Other dates 2028</aside></main></html>"
    ).encode()


class OpnParserTests(SimpleTestCase):
    def test_other_park_profile_and_host_rejected(self):
        from ingestion.bdpn_news import CONFIG as OTHER_CONFIG
        from ingestion.bdpn_news import URL as OTHER_URL

        with self.assertRaises(FetchError):
            parse(
                Source(adapter="web", url=OTHER_URL, config=CONFIG),
                Response(200, page(), {}, OTHER_URL),
            )
        with self.assertRaises(FetchError):
            parse(
                Source(adapter="web", url=URL, config=OTHER_CONFIG),
                Response(200, page(), {}, URL),
            )
        entry = self.perform()[0][0]
        self.assertTrue(entry.url.startswith("https://opn.gov.pl/aktualnosci/"))
        self.assertEqual(entry.metadata["listing_scope"], "opn_initial_news_cards")

    def perform(self, body=None, **kwargs):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(
                kwargs.get("status", 200),
                page() if body is None else body,
                {},
                kwargs.get("url", URL),
            ),
        )

    def test_dates_and_scope_not_title_year_media_or_updated(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (1, 0))
        e = entries[0]
        self.assertEqual((e.published, e.updated), ("2026-09-30", ""))
        self.assertEqual(e.summary, "Skrót źródłowy")
        self.assertFalse(e.metadata["legal_status_verified"])
        self.assertFalse(e.metadata["archive_complete"])
        self.assertNotIn("private.jpg", str(e.metadata))

    def test_stable_identity_and_digest_ignore_position_and_media(self):
        e = self.perform()[0][0]
        other = self.perform(page(card("other") + card()))[0][1]
        self.assertEqual(e.digest(), other.digest())
        self.assertEqual(
            e.digest(), self.perform(page().replace(b"private.jpg", b"changed.jpg"))[0][0].digest()
        )

    def test_dates_fail_closed(self):
        for day, label in [
            ("2026-02-30", "30 lutego 2026"),
            ("2027-09-30", "30 września 2026"),
            ("2026-09-30T12:00:00Z", "30 września 2026"),
            ("2026-09-30", "30 wrzesnia 2026"),
        ]:
            with self.subTest(day=day, label=label), self.assertRaises(FetchError):
                self.perform(page(card(day=day, label=label)))

    def test_structure_identity_links_duplicates_and_budget_fail_closed(self):
        for body in [
            b"\\xff",
            page(""),
            page(card() * 2),
            page(card("https://evil.test")),
            page().replace(b"main-content", b"challenge"),
            page().replace(b"/pl/front-api/news", b"/pl/front-api/events"),
            page().replace(b"</div><aside>", b'<a href="/other">Other</a></div><aside>'),
            page().replace(b"</h1>", b"</h1><h1>Duplicate</h1>"),
            page("".join(card(f"news-{i}") for i in range(101))),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_endpoint_profile_status_guards(self):
        for status, url in [(403, URL), (200, LEGACY)]:
            with self.assertRaises(FetchError):
                self.perform(status=status, url=url)
        with self.assertRaises(FetchError):
            parse(
                Source(adapter="web", url=URL, config={**CONFIG, "admin": True}),
                Response(200, page(), {}, URL),
            )


class OpnConfigurationTests(TestCase):
    def test_ui_scope_and_escaping(self):
        entry = OpnParserTests().perform()[0][0]
        entry.metadata["source_publication_date_text"] = "<script>alert(1)</script>"
        item = Item.objects.create(
            source=self.source,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            metadata=entry.metadata,
            content_hash=entry.digest(),
        )
        response = self.client.get(f"/items/{item.pk}/")
        for value in [
            "BIP, plany ochrony i konsultacje",
            "pierwszą stronę aktualności",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")

    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=151,
            release=release,
            organization="OPN",
            name="News",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-151",
            name="News",
            adapter="web",
            url=LEGACY,
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
        with patch("ingestion.management.commands.configure_opn_news.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_opn_news", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_no_http_preserve_identity_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual((self.source.url, self.source.adapter), (LEGACY, "web"))
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual(
                (self.source.pk, self.source.url, self.source.adapter), (pk, URL, "web")
            )
            self.assertEqual(self.source.config, CONFIG)
            self.assertEqual(self.source.interval_minutes, 360)
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
