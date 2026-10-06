import io
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.eea_press import CONFIG, SLUG, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


def card(slug="first", day="14 Sept 2026"):
    return (
        f'<div class="listing-item"><a href="/en/newsroom/news/{slug}">'
        '<h3 class="listing-header">Title</h3></a>'
        f'<p class="listing-date">{day}</p>'
        '<p class="listing-description">Source excerpt</p></div>'
    )


def body(cards=None):
    return (
        "<html><head><title>Press releases | Newsroom | European Environment Agency (EEA)"
        "</title></head><body><main><h1>Press releases</h1>"
        '<div class="block listing summary"><div class="items item-items">'
        + (cards if cards is not None else card())
        + "</div></div></main></body></html>"
    ).encode()


class EeaPressTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="f" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            registry_id=175,
            release=release,
            master={},
            technical={},
            integration_state="linked",
        )
        self.originals = []
        self.newsroom = RegistryEntry.objects.create(
            registry_id=174,
            release=release,
            master={},
            technical={},
            integration_state="linked",
        )
        for slug, path in (
            ("eea-news", "eeas-press-releases-rss"),
            ("eea-reports", "publications-rss"),
        ):
            source = Source.objects.create(
                slug=slug,
                adapter="feed",
                url=f"https://www.eea.europa.eu/en/newsroom/rss-feeds/{path}/rss.xml",
                enabled=False,
                last_audit={"status": "unverified"},
            )
            self.registry.sources.add(source)
            if slug == "eea-news":
                self.newsroom.sources.add(source)
            self.originals.append(source)
        self.source = Source(adapter="web", url=URL, config=CONFIG)

    def fetch(self, payload=None, target=URL):
        client = Mock()
        client.get.return_value = Response(200, body() if payload is None else payload, {}, target)
        return fetch_entries(self.source, client), client

    def test_live_contract_metadata_and_no_child_get(self):
        (entries, rejected, response, digest), client = self.fetch()
        self.assertEqual(len(entries), 1)
        self.assertEqual(rejected, 0)
        self.assertEqual(entries[0].published, "2026-09-14")
        self.assertEqual(entries[0].updated, "")
        self.assertEqual(entries[0].metadata["listing_scope"], "eea_initial_press_cards")
        self.assertFalse(entries[0].metadata["archive_complete"])
        self.assertEqual(len(digest), 64)
        client.get.assert_called_once()

    def test_invalid_dates_urls_duplicates_and_order_refuse(self):
        for cards in (
            card(day="31 Feb 2026"),
            card(day="14 SepX 2026"),
            card().replace("/en/newsroom/news/first", "https://example.com/first"),
            card() + card(),
            card() + card("second", "15 Sept 2026"),
            card().replace("Title", ""),
            card().replace('class="listing-date"', 'class="changed"'),
        ):
            with self.subTest(cards=cards), self.assertRaises(FetchError):
                self.fetch(body(cards))

    def test_changed_heading_extra_fields_and_empty_or_over_budget_refuse(self):
        for payload in (
            body().replace(b"<h1>Press releases", b"<h1>Other"),
            body().replace(b"<main>", b"<main><h1>Extra</h1>"),
            body(""),
            body("".join(card(f"item-{i}") for i in range(101))),
            body().replace(b"</a>", b'</a><a href="/en/newsroom/news/other">Other</a>'),
        ):
            with self.assertRaises(FetchError):
                self.fetch(payload)

    def test_profile_redirect_utf8_guards(self):
        with self.assertRaises(FetchError):
            self.fetch(target=URL + "/other")
        with self.assertRaises(FetchError):
            self.fetch(b"\xff")
        self.source.config = {"eea_press": "other"}
        with self.assertRaises(FetchError):
            self.fetch()

    def test_summary_and_title_changes_observed_but_images_ignored(self):
        initial = self.fetch()[0][0][0].digest()
        changed = self.fetch(body().replace(b"Source excerpt", b"Changed excerpt"))[0][0][
            0
        ].digest()
        self.assertNotEqual(initial, changed)
        self.assertEqual(
            initial,
            self.fetch(body().replace(b"</main>", b'<img src="https://example.com/a.png"></main>'))[
                0
            ][0][0].digest(),
        )

    def test_configure_dry_apply_repeat_originals_unchanged_no_http(self):
        before = list(Source.objects.order_by("pk").values())
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            call_command("configure_eea_press", stdout=io.StringIO())
            self.assertEqual(Source.objects.count(), 2)
            call_command("configure_eea_press", apply=True, stdout=io.StringIO())
            companion = Source.objects.get(slug=SLUG)
            self.assertFalse(companion.enabled)
            self.assertFalse(companion.activation_approved)
            companion.last_audit = {"status": "verified", "count": 1}
            companion.save()
            call_command("configure_eea_press", apply=True, stdout=io.StringIO())
        self.assertEqual(
            list(
                Source.objects.filter(pk__in=[s.pk for s in self.originals]).order_by("pk").values()
            ),
            before,
        )
        companion.refresh_from_db()
        self.assertEqual(companion.last_audit["status"], "verified")
        self.assertEqual(self.registry.sources.count(), 3)
        self.assertEqual(self.newsroom.sources.count(), 2)
        self.registry.refresh_from_db()
        self.assertEqual(self.registry.integration_state, "linked")

    def test_admin_configuration_or_linkage_refuses(self):
        self.originals[0].config = {"admin": True}
        self.originals[0].save()
        with self.assertRaises(CommandError):
            call_command("configure_eea_press", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), 2)
        self.originals[0].config = {}
        self.originals[0].save()
        self.registry.sources.remove(self.originals[0])
        with self.assertRaises(CommandError):
            call_command("configure_eea_press", apply=True, stdout=io.StringIO())

    def test_history_discloses_scope_and_escapes_source(self):
        entry = self.fetch()[0][0][0]
        self.source.slug = "test-press"
        self.source.save()
        item = Item.objects.create(
            source=self.source,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            content_hash=entry.digest(),
            metadata={**entry.metadata, "source_publication_date_text": "<script>bad</script>"},
        )
        response = self.client.get(f"/items/{item.pk}/")
        self.assertContains(response, "Zakres komunikatu EEA")
        self.assertContains(response, "nie pełne archiwum")
        self.assertContains(response, "&lt;script&gt;bad&lt;/script&gt;")
        self.assertNotContains(response, "<script>bad</script>")
