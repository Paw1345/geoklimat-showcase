import io
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ingestion.adapters import parse
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.opole_bip import profile
from ingestion.transport import FetchError, Response


def fixture(code="notices", count=1, **changes):
    p = profile(code)
    title = changes.get("channel_title", p["title"])
    self_url = changes.get("self_url", p["url"])
    items = []
    for i in range(count):
        link = changes.get("link", f"https://bip.opolskie.pl/2026/09/notice-{i}/")
        identity = changes.get("identity", f"https://bip.opolskie.pl/?p={100 + i}")
        date = changes.get("date", "Fri, 25 Sep 2026 07:58:25 +0000")
        items.append(
            f"<item><title>{escape(changes.get('title', 'Notice'))}</title>"
            f"<link>{escape(link)}</link><guid>{escape(identity)}</guid>"
            f"<pubDate>{date}</pubDate><description>fixture-contact-not-to-store</description>"
            "<author>fixture-person-not-to-store</author></item>"
        )
    return (
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{escape(title)}</title><link>https://bip.opolskie.pl</link>"
        f'<atom:link href="{escape(self_url)}" rel="self" type="application/rss+xml"/>'
        + "".join(items)
        + "</channel></rss>"
    ).encode()


class OpoleFeedTests(SimpleTestCase):
    def perform(self, code="notices", body=None, **changes):
        p = profile(code)
        source = Source(adapter="feed", url=p["url"], config=p["config"])
        response = Response(200, body or fixture(code, **changes), {}, p["url"])
        return parse(source, response)

    def test_both_profiles_preserve_dates_not_documents_or_personal_fields(self):
        for code in ["notices", "register"]:
            entries, rejected = self.perform(code)
            self.assertEqual((len(entries), rejected), (1, 0))
            self.assertEqual(entries[0].published, "Fri, 25 Sep 2026 07:58:25 +0000")
            self.assertEqual(entries[0].updated, "")
            self.assertEqual(entries[0].summary, "")
            self.assertNotIn("authors", entries[0].metadata)
            self.assertEqual(entries[0].metadata["record_kind"], "news_notice")
            self.assertFalse(entries[0].metadata["full_document_downloaded"])

    def test_category_identity_self_link_and_record_host_fail_closed(self):
        for changes in [
            {"channel_title": "General news"},
            {"self_url": "https://bip.opolskie.pl/feed/"},
            {"link": "https://example.com/2026/09/notice/"},
            {"link": "https://bip.opolskie.pl/2026/09/notice/?redirect=1"},
            {"identity": "https://example.com/?p=1"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)

    def test_timezone_title_duplicates_and_budget_fail_closed(self):
        for changes in [
            {"date": "Fri, 25 Sep 2026 07:58:25"},
            {"title": ""},
            {"count": 2, "identity": "https://bip.opolskie.pl/?p=1"},
            {"count": 51},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(body=fixture(**changes))
        with self.assertRaises(FetchError):
            self.perform(body=fixture(count=0))

    def test_response_redirect_and_changed_configuration_are_rejected(self):
        p = profile("notices")
        source = Source(adapter="feed", url=p["url"], config=p["config"])
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, "https://bip.opolskie.pl/feed/"))
        source.config = {**p["config"], "extra": True}
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, p["url"]))


class OpoleSetupTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="source.xlsx", sha256="a" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=132,
            release=release,
            organization="UM Opole",
            name="Environment",
            priority="P2",
            technical_status="VERIFY_ENDPOINT",
            integration_state="needs_endpoint",
            master={},
            technical={},
        )

    def command(self, apply=False):
        with patch("ingestion.management.commands.configure_opole_bip.batch_lock") as lock:
            lock.return_value.__enter__.return_value = True
            call_command("configure_opole_bip", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_gap_and_approval(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.assertEqual(Source.objects.count(), 0)
            self.command(True)
            self.assertEqual(Source.objects.count(), 2)
            self.assertFalse(Source.objects.filter(enabled=True).exists())
            self.assertFalse(Source.objects.filter(activation_approved=True).exists())
            source = Source.objects.get(slug=profile("notices")["slug"])
            source.last_audit = {"status": "verified"}
            source.activation_approved = True
            source.save()
            self.command(True)
            source.refresh_from_db()
            self.assertTrue(source.activation_approved)
            self.assertEqual(source.last_audit, {"status": "verified"})
            self.entry.refresh_from_db()
            self.assertEqual(self.entry.integration_state, "needs_endpoint")

    def test_admin_linkage_rejected_without_partial_setup(self):
        source = Source.objects.create(slug="admin", name="Admin", url="https://example.com/")
        self.entry.sources.add(source)
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.count(), 1)
