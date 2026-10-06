import io
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.normalization import normalized_fields
from ingestion.transport import FetchError, Response
from ingestion.wfos_news import profile


def fixture(key, count=1):
    p = profile(key)
    rows = []
    for i in range(count):
        if key == 101:
            row = (
                f'<article class="item aktualnosc type-aktualnosc status-publish post-{100 + i}" '
                'data-aktualnosc-cats="programy komunikaty">'
                '<time class="date" datetime="2026-09-24T12:10:00+02:00">'
                '24 września 2026</time><h3 class="title">Nabór 25 września</h3>'
                f'<div class="permalink"><a class="btn-read-more" '
                f'href="https://{p["host"]}/aktualnosci/notice-{i}/">Więcej</a></div>'
                '<p class="desc">fixture-not-to-store</p></article>'
            )
        else:
            url = f"https://{p['host']}/ogolne/notice-{i}/"
            row = (
                '<div class="news-box"><div class="news-title" role="heading" '
                f'aria-label="Nabór 25 września"><a href="{url}">Nabór 25 września</a></div>'
                '<div class="news-content-date">24/09/26</div>'
                f'<div class="news-content-link"><a href="{url}">Więcej</a></div>'
                '<p class="news-content">fixture-not-to-store</p></div>'
            )
        rows.append(row)
    body = "".join(rows)
    if key == 101:
        body = '<main class="site-main"><header class="header"><h2 class="title">'
        body += 'Aktualności</h2></header><div class="aktualnosci-js-items">'
        body += "".join(rows) + "</div></main>"
    else:
        body = '<div class="main-single-content"><h1 class="last-head">Aktualności/komunikaty'
        body += '</h1><div class="main-archive-page">' + "".join(rows) + "</div></div>"
    return f"<html><head><title>{escape(p['title'])}</title></head>{body}</html>".encode()


class WfosNewsParserTests(SimpleTestCase):
    def perform(self, key, body=None, count=1):
        p = profile(key)
        source = Source(adapter="web", url=p["url"], config=p["config"])
        return parse(
            source, Response(200, body if body is not None else fixture(key, count), {}, p["url"])
        )

    def test_metadata_source_dates_and_no_open_call_inference(self):
        for key, precision in [(101, "time"), (102, "day")]:
            with self.subTest(key=key):
                rows, rejected = self.perform(key)
                self.assertEqual((len(rows), rejected), (1, 0))
                row = rows[0]
                self.assertTrue(row.published.startswith("2026-09-24"))
                self.assertEqual((row.summary, row.updated), ("", ""))
                self.assertNotIn("authors", row.metadata)
                self.assertFalse(row.metadata["funding_call_verified"])
                self.assertFalse(row.metadata["full_document_downloaded"])
                item = Item(
                    source=Source(language="pl"),
                    url=row.url,
                    title=row.title,
                    metadata=row.metadata,
                )
                self.assertEqual(normalized_fields(item)["date_precision"], precision)
                self.assertNotIn("fixture-not-to-store", str(row))

    def test_inline_archive_bounded_latest_twenty_and_tail_validation(self):
        self.assertEqual(len(self.perform(101, count=25)[0]), 20)
        for count in [0, 1001]:
            with self.assertRaises(FetchError):
                self.perform(101, count=count)
        body = fixture(101, 25).replace(b"post-124", b"post-100")
        with self.assertRaises(FetchError):
            self.perform(101, body)
        for count in [0, 51]:
            with self.assertRaises(FetchError):
                self.perform(102, count=count)

    def test_bad_dates_order_duplicates_and_ambiguous_fields(self):
        cases = {
            101: [
                (b"2026-09-24T12:10:00+02:00", b"2026-09-24T12:10:00"),
                ("24 września 2026".encode(), "25 września 2026".encode()),
                (b"status-publish", b"status-draft"),
                (b"programy komunikaty", b"programy unknown"),
                (b'<h3 class="title">', b'<h3 class="title">extra</h3><h3 class="title">'),
            ],
            102: [
                (b"24/09/26", b"31/02/26"),
                (b"24/09/26", b"24/09/2026"),
                (b'role="heading"', b'role="none"'),
                (
                    b'<div class="news-content-date">',
                    b'<div class="news-content-date">24/09/26</div><div class="news-content-date">',
                ),
            ],
        }
        for key, replacements in cases.items():
            for old, new in replacements:
                with self.subTest(key=key, new=new), self.assertRaises(FetchError):
                    self.perform(key, fixture(key).replace(old, new))
            with self.assertRaises(FetchError):
                self.perform(key, fixture(key, 2).replace(b"notice-1", b"notice-0"))
        with self.assertRaises(FetchError):
            self.perform(102, fixture(102, 2).replace(b"24/09/26", b"23/09/26", 1))
        with self.assertRaises(FetchError):
            self.perform(101, fixture(101, 2).replace(b"T12:10:00", b"T11:10:00", 1))

    def test_endpoint_host_page_identity_and_utf8_fail_closed(self):
        for key in [101, 102]:
            p = profile(key)
            for old, new in [
                (p["host"].encode(), b"example.com"),
                (b"https://", b"http://"),
                (b"notice-0/", b"notice-0/?page=2"),
                (b"notice-0/", b"notice-0/#part"),
                (escape(p["title"]).encode(), b"Other institution"),
            ]:
                with self.subTest(key=key, old=old), self.assertRaises(FetchError):
                    self.perform(key, fixture(key).replace(old, new))
            source = Source(adapter="web", url=p["url"], config=p["config"])
            for response in [
                Response(404, fixture(key), {}, p["url"]),
                Response(200, fixture(key), {}, p["legacy"]),
                Response(200, fixture(key) + b"\xff", {}, p["url"]),
            ]:
                with self.assertRaises(FetchError):
                    parse(source, response)
            source.config = {**p["config"], "extra": True}
            with self.assertRaises(FetchError):
                parse(source, Response(200, fixture(key), {}, p["url"]))

    def test_zgora_stable_id_and_lodz_conflicting_link(self):
        nbsp = fixture(102).replace(
            b'aria-label="Nab\xc3\xb3r 25', 'aria-label="Nabór\u00a025'.encode()
        )
        self.assertEqual(len(self.perform(102, nbsp)[0]), 1)
        original = self.perform(101)[0][0]
        changed = self.perform(101, fixture(101).replace(b"notice-0/", b"changed-slug/"))[0][0]
        self.assertEqual(original.key, changed.key)
        with self.assertRaises(FetchError):
            self.perform(102, fixture(102).replace(b"notice-0/", b"changed/", 1))


class WfosNewsSetupTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="registry.xlsx", sha256="e" * 64, sheets={}
        )
        self.sources = []
        for key in [101, 102]:
            p = profile(key)
            entry = RegistryEntry.objects.create(
                registry_id=key,
                release=release,
                organization="WFOS",
                name="News",
                priority="P2",
                technical_status="VERIFY_ENDPOINT",
                integration_state="configured",
                master={},
                technical={},
            )
            source = Source.objects.create(
                slug=p["slug"],
                name="News",
                adapter="web",
                url=p["legacy"],
                config={},
                enabled=False,
                activation_approved=False,
                interval_minutes=120,
                scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
                "Bez pełnego archiwum.",
                etag="old",
                last_audit={"status": "unverified"},
            )
            entry.sources.add(source)
            self.sources.append(source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_wfos_news.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_wfos_news", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_identity_audit_and_approval(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.assertEqual(
                list(Source.objects.order_by("slug").values_list("url", flat=True)),
                [profile(k)["legacy"] for k in [101, 102]],
            )
            self.command(True)
            for source, key in zip(self.sources, [101, 102], strict=True):
                source.refresh_from_db()
                self.assertEqual(source.url, profile(key)["url"])
                self.assertEqual((source.last_audit, source.etag), ({}, ""))
                self.assertFalse(source.enabled)
                source.enabled = source.activation_approved = True
                source.last_audit = {"status": "verified"}
                source.save()
            self.command(True)
            self.assertEqual(Source.objects.count(), 2)
            for source in self.sources:
                source.refresh_from_db()
                self.assertTrue(source.enabled)
                self.assertTrue(source.activation_approved)
                self.assertEqual(source.last_audit, {"status": "verified"})

    def test_second_source_admin_changes_prevent_entire_package(self):
        source = self.sources[1]
        for field, value in [
            ("config", {"admin": True}),
            ("enabled", True),
            ("last_success_at", timezone.now()),
            ("failures", 1),
        ]:
            original = getattr(source, field)
            setattr(source, field, value)
            source.save()
            with self.assertRaises(CommandError):
                self.command(True)
            self.assertEqual(Source.objects.get(pk=self.sources[0].pk).url, profile(101)["legacy"])
            setattr(source, field, original)
            source.save()

    def test_busy_or_foreign_linkage_no_writes(self):
        with self.assertRaises(CommandError):
            self.command(True, acquired=False)
        self.sources[1].registry_entries.first().sources.add(
            Source.objects.create(slug="admin", name="Admin", url=profile(102)["url"])
        )
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.get(pk=self.sources[0].pk).url, profile(101)["legacy"])
