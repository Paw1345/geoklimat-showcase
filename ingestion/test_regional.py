import io
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.adapters import parse
from ingestion.models import RegistryEntry, RegistryRelease, Source
from ingestion.regional_profiles import PROFILES, polish_date
from ingestion.registry_profiles import profile
from ingestion.service import fetch_entries
from ingestion.transport import FetchError, Response


class RegionalTests(TestCase):
    def source(self, key):
        adapter, url, config = PROFILES[key]
        return Source(adapter=adapter, url=url, config=config.copy())

    def parse(self, key, body, response_url=None):
        source = self.source(key)
        return parse(source, Response(200, body.encode(), {}, response_url or source.url))

    def test_all_four_reviewed_html_structures(self):
        examples = {
            126: '<article class="post"><a class="entry-permalink" title="News" '
            'href="/srodowisko/new">Image <h3>News</h3>'
            '<div class="entry-description">Description</div></a></article>',
            128: '<main><div class="tile-content"><a class="tile-title" '
            'href="/wiadomosci/1/news">News</a>'
            '<span class="tile-date">3.07.2026</span></div></main>',
            129: '<article class="catItemView"><h3 class="catItemTitle">'
            '<a href="/srodowisko/news">News</a></h3>'
            '<div class="catItemDateCreated">wtorek, 22 września 2026</div>'
            '<div class="catItemIntroText">Description</div></article>',
            137: '<article class="clist_box1a"><a class="clist_box1b" href="/news/">'
            '<time datetime="2026-09-22">wtorek, 22 września 2026</time>'
            '<h3 class="clist_title">News</h3></a></article>',
        }
        for key, html in examples.items():
            with self.subTest(key=key):
                entries, rejected = self.parse(key, html)
                self.assertEqual((len(entries), rejected), (1, 0))
                self.assertEqual(entries[0].title, "News")
                self.assertEqual(entries[0].metadata["record_kind"], "news")
                self.assertEqual(
                    entries[0].published,
                    "" if key == 126 else "2026-07-03" if key == 128 else "2026-09-22",
                )
                with self.assertRaises(FetchError):
                    self.parse(key, html + html)

    def test_missing_or_changed_section_and_external_links_fail_closed(self):
        good = (
            '<article class="post"><a class="entry-permalink" href="/srodowisko/new" '
            'title="News">News</a></article>'
        )
        for html, url in [
            ("<main>Generic homepage</main>", None),
            (good.replace("/srodowisko/new", "https://outside.example/news"), None),
            (good, "https://kujawsko-pomorskie.pl/"),
            (good.replace("href=", "nohref="), None),
        ]:
            with self.subTest(url=url), self.assertRaises(FetchError):
                self.parse(126, html, url)

    def test_polish_date_and_missing_day_rejected(self):
        self.assertEqual(polish_date("Środa, 13 Sierpnia 2026"), "2026-08-13")
        for value in ["31 lutego 2026", "wczoraj", "wrzesień 2026"]:
            with self.assertRaises(FetchError):
                polish_date(value)

    def test_torun_title_summary_and_precise_source_timestamp(self):
        html = (
            '<div class="wiersza-aktualnosci-sp2 views-row"><span class="zajawka">'
            '<a href="/aktualnosc/1554-news"><span class="podtytul">News…</span>'
            '<time class="datetime" datetime="2026-09-16T12:59:25+02:00">16/09/2026</time>'
            '<span class="info">Description</span></a></span></div>'
        )
        entries, rejected = self.parse(99, html)
        self.assertEqual(rejected, 0)
        self.assertEqual(entries[0].title, "News…")
        self.assertEqual(entries[0].summary, "Description")
        self.assertEqual(entries[0].published, "2026-09-16T12:59:25+02:00")
        for changed in (
            html + html,
            html.replace('datetime="2026-09-16T12:59:25+02:00"', ""),
            html.replace("2026-09-16T12:59:25+02:00", "yesterday"),
            html.replace('class="podtytul"', 'class="changed"'),
            html.replace("/aktualnosc/1554-news", "https://outside.example/news"),
        ):
            with self.subTest(html=changed), self.assertRaises(FetchError):
                self.parse(99, changed)
        with self.assertRaises(FetchError):
            self.parse(99, html, "https://wfosigw.torun.pl/")

    def test_feed_redirect_cannot_silently_become_homepage(self):
        source = self.source(84)
        client = Mock()
        client.get.return_value = Response(200, b"<html></html>", {}, "https://www.wios.lublin.pl/")
        with self.assertRaisesMessage(FetchError, "configured endpoint"):
            fetch_entries(source, client)

    def test_podkarpackie_feed_scope_and_bounded_archive(self):
        def document(count):
            items = "".join(
                "<item><title>News</title><link>"
                f"https://podkarpackie.pl/index.php/srodowisko/aktualnosci/news-{i}"
                "</link><pubDate>Mon, 28 Sep 2026 08:24:15 +0200</pubDate></item>"
                for i in range(count)
            )
            return (
                '<rss version="2.0"><channel><title>Portal Samorządu Województwa '
                "Podkarpackiego - Aktualności</title>" + items + "</channel></rss>"
            )

        entries, rejected = self.parse(133, document(175))
        self.assertEqual((len(entries), rejected), (175, 0))
        self.assertEqual(entries[0].published, "Mon, 28 Sep 2026 08:24:15 +0200")
        for body in (
            document(201),
            document(1).replace("/srodowisko/", "/sport/"),
            document(1).replace("podkarpackie.pl/index.php", "outside.example/index.php"),
        ):
            with self.subTest(body=body), self.assertRaises(FetchError):
                self.parse(133, body)
        with self.assertRaises(FetchError):
            self.parse(133, document(1), "https://podkarpackie.pl/")

    def test_zachodniopomorskie_news_and_notices_stay_environmental(self):
        def row(kind, slug):
            base = f"https://wzp.pl/aktualnosci/{kind}/ekologia-srodowisko/"
            return (
                '<article class="p-news-tiles-item">'
                '<span class="p-news-tiles-item__date">23 września 2026</span>'
                f'<a class="p-news-tiles-item__category" href="{base}">Ekologia i środowisko</a>'
                f'<h3 class="p-news-tiles-item__title"><a href="{base}{slug}/">News</a></h3>'
                "</article>"
            )

        html = row("newsy", "one") + row("komunikaty-urzedowe", "two")
        entries, rejected = self.parse(140, html)
        self.assertEqual((len(entries), rejected), (2, 0))
        self.assertEqual(entries[0].published, "2026-09-23")
        self.assertEqual(entries[0].summary, "")
        for body in (
            html + row("newsy", "one"),
            html.replace("Ekologia i środowisko", "Sport"),
            html.replace("/ekologia-srodowisko/", "/sport/"),
            html.replace("23 września 2026", "yesterday"),
            html.replace("https://wzp.pl/", "https://outside.example/"),
        ):
            with self.subTest(body=body), self.assertRaises(FetchError):
                self.parse(140, body)
        with self.assertRaises(FetchError):
            self.parse(140, html, "https://wzp.pl/")

    def test_lubelskie_feed_preserves_date_and_rejects_wrong_category(self):
        item = (
            "<item><title>News</title><link>https://www.lubelskie.pl/aktualnosci/news/</link>"
            "<pubDate>Thu, 24 Sep 2026 09:12:01 +0000</pubDate>"
            "<category>Aktualności środowisko</category><description>Description</description>"
            "</item>"
        )

        def document(items):
            return (
                '<rss version="2.0"><channel><title>Aktualności środowisko – UM</title>'
                + items
                + "</channel></rss>"
            )

        entries, rejected = self.parse(127, document(item))
        self.assertEqual((len(entries), rejected), (1, 0))
        self.assertEqual(entries[0].published, "Thu, 24 Sep 2026 09:12:01 +0000")
        self.assertEqual(entries[0].summary, "Description")
        for changed in (
            item + item,
            item.replace("Aktualności środowisko", "Sport"),
            item.replace("www.lubelskie.pl", "outside.example"),
            item.replace("/aktualnosci/news/", "/sport/news/"),
            item.replace("Thu, 24 Sep 2026 09:12:01 +0000", "yesterday"),
        ):
            with self.subTest(item=changed), self.assertRaises(FetchError):
                self.parse(127, document(changed))
        with self.assertRaises(FetchError):
            self.parse(127, document(item), "https://www.lubelskie.pl/feed/")

    def test_malopolskie_environment_filter_and_utc_timestamp(self):
        html = (
            '<main><div class="wiersza-aktualnosci-sp2 views-row"><span class="zajawka">'
            '<a href="/aktualnosci/srodowisko/news?v=news">'
            '<span class="kategoria-tresci">Środowisko</span>'
            '<span class="podtytul">News</span>'
            '<time class="datetime" datetime="2026-09-14T08:57:58Z">14 września</time>'
            '<span class="info">Description</span></a></span></div></main>'
        )
        entries, rejected = self.parse(130, html)
        self.assertEqual(rejected, 0)
        self.assertEqual(entries[0].title, "News")
        self.assertEqual(entries[0].summary, "Description")
        self.assertEqual(entries[0].published, "2026-09-14T08:57:58Z")
        for changed in (
            html + html,
            html.replace("Środowisko", "Sport"),
            html.replace("/aktualnosci/srodowisko/", "/aktualnosci/sport/"),
            html.replace('class="podtytul"', 'class="changed"'),
            html.replace('datetime="2026-09-14T08:57:58Z"', ""),
            html.replace("2026-09-14T08:57:58Z", "2026-09-14"),
            html.replace("/aktualnosci/srodowisko/news?v=news", "https://outside.example/news"),
        ):
            with self.subTest(html=changed), self.assertRaises(FetchError):
                self.parse(130, changed)
        with self.assertRaises(FetchError):
            self.parse(130, html, "https://www.malopolska.pl/aktualnosci")

    def test_fund_feeds_use_reviewed_canonical_hosts_and_reject_redirects(self):
        for key in (106, 107):
            source = self.source(key)
            self.assertEqual(source.adapter, "feed")
            self.assertTrue(source.config["strict_endpoint"])
            client = Mock()
            client.get.return_value = Response(200, b"<html>home</html>", {}, source.url + "/other")
            with self.subTest(key=key), self.assertRaises(FetchError):
                fetch_entries(source, client)

    def test_configure_and_import_profiles_preserve_admin_changes(self):
        release = RegistryRelease.objects.create(filename="test", sha256="e" * 64, sheets={})
        for key in PROFILES:
            RegistryEntry.objects.create(
                registry_id=key, release=release, priority="P2", master={}, technical={}
            )
            route, state, _ = profile({"ID": key}, {})
            self.assertEqual(route, PROFILES[key])
            self.assertEqual(state, "configured")
        call_command("configure_regional", stdout=io.StringIO())
        self.assertFalse(Source.objects.exists())
        call_command("configure_regional", apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), len(PROFILES))
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        call_command("configure_regional", apply=True, stdout=io.StringIO())
        source = Source.objects.get(slug="registry-084")
        source.url = "https://example.org/admin"
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_regional", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertEqual(source.url, "https://example.org/admin")

    def test_pomorskie_relative_age_is_not_invented_date(self):
        html = (
            '<a class="article-item" href="/news/">'
            '<div class="date">5 dni temu</div>'
            '<div class="article-title"><h3>News</h3></div></a>'
        )
        entries, _ = self.parse(135, html)
        self.assertEqual(entries[0].title, "News")
        self.assertEqual(entries[0].published, "")

    def test_wielkopolskie_extracts_heading_without_date_prefix(self):
        html = (
            '<main><div class="news-lista-elem"><a class="link-heading" href="/artykul/news">'
            '<div class="z-bigger-date"><p>13 Sierpnia 2026</p></div>'
            '<h3>News</h3><p class="size-17">Description</p></a></div></main>'
        )
        entries, _ = self.parse(139, html)
        self.assertEqual(entries[0].title, "News")
        self.assertEqual(entries[0].published, "2026-08-13")
