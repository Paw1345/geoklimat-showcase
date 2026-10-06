import io
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.regional_rss import PROFILES, profile
from ingestion.transport import FetchError, Response


def fixture(key=82, count=1, **changes):
    p = profile(key)
    host = p["home"].rstrip("/")
    items = []
    for i in range(count):
        path = f"/2026/09/29/notice-{i}/" if p.get("dated_path") else f"/notice-{i}/"
        link = changes.get("link", host + path)
        identity = changes.get("identity", host + f"/?p={100 + i}")
        date = changes.get("date", "Tue, 29 Sep 2026 13:21:03 +0000")
        updated = changes.get("updated", "")
        items.append(
            f"<item><title>{escape(changes.get('title', 'Official notice'))}</title>"
            f"<link>{escape(link)}</link><guid>{escape(identity)}</guid>"
            f"<pubDate>{escape(date)}</pubDate>"
            f"<category>{escape(changes.get('category', 'Aktualności'))}</category>"
            + (f"<atom:updated>{updated}</atom:updated>" if updated else "")
            + "<description>fixture-contact</description><author>fixture-person</author></item>"
        )
    return (
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{escape(changes.get('feed_title', p['title']))}</title>"
        f"<link>{escape(changes.get('home', p['home']))}</link>"
        f'<atom:link href="{escape(changes.get("self_url", p["url"]))}" rel="self"/>'
        + "".join(items)
        + "</channel></rss>"
    ).encode()


class RegionalRssTests(SimpleTestCase):
    def perform(self, key=82, **changes):
        p = profile(key)
        source = Source(adapter="feed", url=p["url"], config=p["config"])
        return parse(source, Response(200, fixture(key, **changes), {}, p["url"]))

    def test_profiles_metadata_only_no_fallback_updated_or_funding_claims(self):
        for key in PROFILES:
            rows, rejected = self.perform(key)
            self.assertEqual((len(rows), rejected), (1, 0))
            self.assertEqual(rows[0].summary, "")
            self.assertEqual(rows[0].updated, "")
            self.assertNotIn("authors", rows[0].metadata)
            self.assertFalse(rows[0].metadata["funding_call_verified"])
            self.assertFalse(rows[0].metadata["full_document_downloaded"])
            self.assertEqual(
                rows[0].metadata["publication_date_raw"], "Tue, 29 Sep 2026 13:21:03 +0000"
            )

    def test_channel_category_host_and_guid_fail_closed(self):
        for changes in [
            {"feed_title": "Other institution"},
            {"home": "https://example.com"},
            {"self_url": "https://www.wroclaw.wios.gov.pl/comments/feed/"},
            {"category": "Comments"},
            {"link": "https://example.com/notice/"},
            {"link": "https://www.wroclaw.wios.gov.pl/notice/?q=1"},
            {"link": "https://www.wroclaw.wios.gov.pl/notice/#comments"},
            {"identity": "https://example.com/?p=1"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)

    def test_dates_titles_duplicates_and_window_budget(self):
        for changes in [
            {"date": ""},
            {"date": "Tue, 29 Sep 2026 13:21:03"},
            {"title": ""},
            {"count": 2, "identity": "https://www.wroclaw.wios.gov.pl/?p=1"},
            {"count": 2, "link": "https://www.wroclaw.wios.gov.pl/notice/"},
            {"count": 0},
            {"count": 51},
            {"updated": "invalid"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(FetchError):
                self.perform(**changes)

    def test_configuration_response_identity_and_unknown_profile(self):
        p = profile(82)
        for key in [True, "82", 0]:
            with self.assertRaises(FetchError):
                profile(key)
        source = Source(adapter="feed", url=p["url"], config=p["config"])
        for response in [
            Response(200, fixture(), {}, p["home"]),
            Response(404, fixture(), {}, p["url"]),
        ]:
            with self.assertRaises(FetchError):
                parse(source, response)
        source.config = {**p["config"], "extra": True}
        with self.assertRaises(FetchError):
            parse(source, Response(200, fixture(), {}, p["url"]))

    def test_poznan_preserves_uncategorized_without_weakening_other_profiles(self):
        rows, rejected = self.perform(96, category="Bez kategorii")
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0].metadata["source_categories"], ["Bez kategorii"])
        self.assertEqual(rows[0].metadata["record_kind"], "news_notice")
        for category in ["Aktualności", "Komunikaty"]:
            self.assertEqual(len(self.perform(96, category=category)[0]), 1)
        with self.assertRaises(FetchError):
            self.perform(96, category="Other")
        with self.assertRaises(FetchError):
            self.perform(94, category="Bez kategorii")
        self.assertNotIn("source_categories", self.perform(82)[0][0].metadata)

    def test_poznan_dated_links_remain_exact_host_and_path(self):
        for link in [
            "https://poznan.wios.gov.pl/notice/",
            "https://poznan.wios.gov.pl/2026/13/01/notice/",
            "https://poznan.wios.gov.pl/2026/09/00/notice/",
            "https://www.poznan.wios.gov.pl/2026/09/29/notice/",
            "https://poznan.wios.gov.pl/2026/09/29/notice/?q=1",
        ]:
            with self.subTest(link=link), self.assertRaises(FetchError):
                self.perform(96, link=link)

    def test_katowice_reviewed_tracking_only_without_other_profile_relaxation(self):
        base = "https://wfosigw.katowice.pl/notice/"
        query = "utm_source=rss&utm_medium=rss&utm_campaign=notice"
        rows, rejected = self.perform(109, link=base + "?" + query)
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0].url, base)
        self.assertEqual(rows[0].key, self.perform(109, link=base)[0][0].key)
        for suffix in [
            query + "&page=2",
            query + "&utm_source=rss",
            query.replace("utm_source=rss", "utm_source=other"),
            query.replace("utm_campaign=notice", "utm_campaign=other"),
            "utm_source=rss&utm_medium=rss",
            "page=2",
            "utm_source",
        ]:
            with self.subTest(suffix=suffix), self.assertRaises(FetchError):
                self.perform(109, link=base + "?" + suffix)
        for key in [82, 94, 96, 108, 111]:
            p = profile(key)
            path = "/2026/09/29/notice/" if p.get("dated_path") else "/notice/"
            with self.subTest(key=key), self.assertRaises(FetchError):
                self.perform(key, link=p["home"].rstrip("/") + path + "?" + query)

    def test_olsztyn_dated_path_publication_independent_of_title(self):
        rows, rejected = self.perform(111, title="Urząd nieczynny 2–6 października")
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0].published, "Tue, 29 Sep 2026 13:21:03 +0000")
        for link in [
            "https://wfosigw.olsztyn.pl/notice/",
            "https://wfosigw.olsztyn.pl/2026/13/29/notice/",
            "https://www.wfosigw.olsztyn.pl/2026/09/29/notice/",
            "https://wfosigw.olsztyn.pl/2026/09/29/notice/#part",
        ]:
            with self.subTest(link=link), self.assertRaises(FetchError):
                self.perform(111, link=link)


class RegionalRssSetupTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="registry.xlsx", sha256="b" * 64, sheets={}
        )
        for key in PROFILES:
            p = profile(key)
            entry = RegistryEntry.objects.create(
                registry_id=key,
                release=release,
                organization=p["title"],
                name="News",
                priority="P2",
                technical_status="VERIFY_ENDPOINT",
                integration_state="configured",
                master={},
                technical={},
            )
            source = Source.objects.create(
                slug=p["slug"],
                name=p["title"],
                url=p.get("legacy_home", p["home"]),
                adapter="web",
                config={},
                enabled=False,
                activation_approved=False,
                interval_minutes=120,
                scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
                "Bez pełnego archiwum.",
                last_audit={"status": "verified", "rejected": 1},
                audited_at=timezone.now(),
                etag="old",
            )
            entry.sources.add(source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_regional_rss.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_regional_rss", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_approval_and_identity(self):
        ids = list(Source.objects.values_list("pk", flat=True))
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.assertEqual(Source.objects.filter(adapter="web").count(), len(PROFILES))
            self.command(True)
            self.assertEqual(list(Source.objects.values_list("pk", flat=True)), ids)
            for source in Source.objects.all():
                self.assertFalse(source.enabled)
                self.assertFalse(source.activation_approved)
                self.assertEqual(source.last_audit, {})
                self.assertIsNone(source.audited_at)
                self.assertEqual(source.etag, "")
            Source.objects.filter(slug="registry-082").update(
                enabled=True, activation_approved=True, last_audit={"status": "verified"}
            )
            self.command(True)
            source = Source.objects.get(slug="registry-082")
            self.assertTrue(source.enabled)
            self.assertEqual(source.last_audit, {"status": "verified"})
            self.assertEqual(
                RegistryEntry.objects.filter(integration_state="configured").count(), len(PROFILES)
            )

    def test_second_admin_change_prevents_partial_package_write(self):
        Source.objects.filter(slug="registry-108").update(config={"admin": True})
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.filter(adapter="web").count(), len(PROFILES))

    def test_busy_linkage_and_active_history_are_preserved(self):
        with self.assertRaises(CommandError):
            self.command(True, acquired=False)
        Source.objects.filter(slug="registry-108").update(last_success_at=timezone.now())
        with self.assertRaises(CommandError):
            self.command(True)
        Source.objects.filter(slug="registry-108").update(last_success_at=None)
        entry = RegistryEntry.objects.get(registry_id=82)
        entry.sources.add(Source.objects.get(slug="registry-108"))
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.filter(adapter="web").count(), len(PROFILES))

    def test_new_fund_sources_keep_admin_state_and_source_ids(self):
        for key in [109, 111]:
            s = Source.objects.get(slug=profile(key)["slug"])
            s.config = {"admin": True}
            s.save()
            with self.assertRaises(CommandError):
                self.command(True)
            self.assertEqual(Source.objects.filter(adapter="web").count(), len(PROFILES))
            s.config = {}
            s.save()
        ids = dict(Source.objects.values_list("slug", "pk"))
        self.command(True)
        Source.objects.filter(slug__in=["registry-109", "registry-111"]).update(
            enabled=True, activation_approved=True, last_audit={"status": "verified"}
        )
        self.command(True)
        for key in [109, 111]:
            s = Source.objects.get(slug=profile(key)["slug"])
            self.assertEqual(s.pk, ids[s.slug])
            self.assertTrue(s.enabled)
            self.assertTrue(s.activation_approved)
            self.assertEqual(s.last_audit, {"status": "verified"})
