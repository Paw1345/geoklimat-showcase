import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.gus_publications import BANNER, CONFIG, HEADERS, PAGE_TITLE, SUCCESSOR, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def document(
    category="srodowisko",
    series="11",
    edition="8",
    ordinal=1,
    title="Ochrona środowiska 2025",
    day="31.08.2026",
    kind="Publikacja",
    slug="ochrona-srodowiska",
):
    url = f"{URL}{category}/{slug},{series},{edition}.html"
    return (
        f"<tr><td>{ordinal}</td><td>{kind}</td>"
        f'<td><a href="{url}" title="{title}">{title}</a></td>'
        f'<td>{day}</td><td><a href="{url}#archive">Archiwum</a></td></tr>'
    )


def page(environment=None, energy=None):
    headers = "".join(f"<th>{h}</th>" for h in HEADERS)
    tables = ""
    for category, rows in [
        ("Środowisko", document() if environment is None else environment),
        ("Energia", document(category="energia", kind="") if energy is None else energy),
    ]:
        tables += (
            f'<h2>{category}</h2><table title="{category} tabela">'
            f"<thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>"
        )
    return (
        f"<html><head><title>{PAGE_TITLE}</title></head><body>"
        f'<a href="{SUCCESSOR}">{BANNER}</a><section id="content-href">'
        '<div class="header-block"><h2 class="title">Środowisko. Energia</h2></div>'
        f"{tables}</section></body></html>"
    ).encode()


class GusParserTests(SimpleTestCase):
    def perform(self, body=None, **changes):
        source = Source(adapter="web", url=URL, config=CONFIG)
        return parse(source, Response(200, page() if body is None else body, {}, URL), **changes)

    def test_dates_types_identity_and_metadata_only(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (2, 0))
        self.assertEqual(entries[0].published, "2026-08-31")
        self.assertEqual(entries[0].metadata["source_publication_date_text"], "31.08.2026")
        self.assertEqual(entries[1].metadata["source_document_type"], "")
        self.assertNotEqual(entries[0].key, entries[1].key)
        for entry in entries:
            self.assertEqual((entry.summary, entry.updated), ("", ""))
            self.assertFalse(entry.metadata["numeric_statistics_downloaded"])
            self.assertFalse(entry.metadata["full_document_downloaded"])
            self.assertTrue(entry.metadata["archive_available"])
            self.assertNotIn("authors", entry.metadata)

    def test_title_collision_distinct_series_and_stable_id(self):
        body = page(environment=document() + document(series="12", ordinal=2))
        entries, rejected = self.perform(body)
        self.assertEqual((len(entries), rejected), (3, 0))
        self.assertEqual(entries[0].title, entries[1].title)
        self.assertNotEqual(entries[0].key, entries[1].key)
        changed = self.perform(page(environment=document(slug="changed", title="Changed")))[0][0]
        self.assertEqual(changed.key, entries[0].key)

    def test_list_position_is_not_content_change(self):
        original = self.perform()[0][0]
        entries = self.perform(page(environment=document(series="99") + document(ordinal=2)))[0]
        self.assertEqual(original.digest(), entries[1].digest())

    def test_related_official_sections_have_separate_identity(self):
        old = URL + "srodowisko/"
        related = [
            "obszary-tematyczne/inne-opracowania/inne-opracowania-zbiorcze",
            "infografiki-widzety/infografiki",
        ]
        rows = "".join(
            document(series="39", edition="1", ordinal=n).replace(
                old, "https://stat.gov.pl/" + namespace + "/"
            )
            for n, namespace in enumerate(related, 1)
        )
        entries, rejected = self.perform(page(environment=rows))
        self.assertEqual((len(entries), rejected), (3, 0))
        self.assertNotEqual(entries[0].key, entries[1].key)
        for replacement in [
            "https://stat.gov.pl/unreviewed/",
            "https://other.gov.pl/infografiki-widzety/infografiki/",
        ]:
            with self.assertRaises(FetchError):
                self.perform(page(environment=document().replace(old, replacement)))

    def test_page_containers_headers_banner_and_encoding_fail_closed(self):
        replacements = [
            (PAGE_TITLE, "Challenge"),
            ("content-href", "other"),
            ("Środowisko tabela", "Other tabela"),
            ("Tytuł dokumentu", "Other header"),
            (BANNER, "Other banner"),
            (SUCCESSOR, "https://example.com/"),
        ]
        for old, new in replacements:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page(environment="")]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_document_fields_calendar_duplicates_and_budgets_fail_closed(self):
        cases = [
            document(day="31.02.2026"),
            document(day="2026-08-31"),
            document(kind="Unknown"),
            document(ordinal=2),
            document() + document(ordinal=2),
            document(day="30.08.2026") + document(series="12", ordinal=2),
            "".join(document(series=str(n), ordinal=n) for n in range(1, 202)),
        ]
        for rows in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(FetchError):
                self.perform(page(environment=rows))
        for old, new in [
            (URL, "https://example.com/"),
            (",11,8.html", ",0,8.html"),
            (".html#archive", ".html?download=1"),
            ('title="Ochrona środowiska 2025"', 'title="Other"'),
            ("<td>31.08.2026</td>", '<td colspan="2">31.08.2026</td><td>extra</td>'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))

    def test_endpoint_status_and_profile_fail_closed(self):
        for config, status, url in [
            ({**CONFIG, "admin": True}, 200, URL),
            (CONFIG, 304, URL),
            (CONFIG, 200, SUCCESSOR),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=URL, config=config), Response(status, page(), {}, url)
                )


class GusConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=42,
            release=release,
            organization="GUS",
            name="GUS",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-042",
            name="GUS",
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
        with patch("ingestion.management.commands.configure_gus_publications.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_gus_publications", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_no_http_preserve_admin_identity_audit(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.config, {})
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual((self.source.pk, self.source.config), (pk, CONFIG))
            self.assertEqual(self.source.last_audit, {})
            self.assertEqual(self.source.interval_minutes, 360)
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})
            self.assertEqual(Source.objects.count(), 1)

    def test_busy_admin_history_linkage_guards(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        for field, value in [
            ("config", {"admin": True}),
            ("enabled", True),
            ("activation_approved", True),
            ("last_success_at", timezone.now()),
            ("scope", "Custom"),
            ("failures", 1),
            ("interval_minutes", 60),
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

    def test_extra_registry_linkage_is_preserved(self):
        self.entry.sources.add(Source.objects.create(slug="admin", name="Admin", url=URL))
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(Source.objects.get(pk=self.source.pk).config, {})

    def test_item_view_discloses_legacy_and_escapes(self):
        entry = GusParserTests().perform()[0][1]
        entry.metadata["source_category"] = "<script>alert(1)</script>"
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
        for text in [
            "Nie podano",
            "Dotychczasowy portal GUS",
            "danych liczbowych",
            "nie zostało jeszcze zweryfikowane",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, text)
        self.assertNotContains(response, "<script>alert(1)</script>")
