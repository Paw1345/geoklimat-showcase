import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.opn_documents import CONFIG, ISAP, SLUG, URL
from ingestion.opn_news import CONFIG as NEWS_CONFIG
from ingestion.opn_news import URL as NEWS_URL
from ingestion.transport import FetchError, Response

IDENTIFIER = "f037a270-59a2-44d1-92e3-9441b8460ada"


def row(identifier=IDENTIFIER, title="Plan ochrony 2021"):
    return (
        '<li class="downloads__item">'
        '<a class="downloads__item-name" '
        f'href="/pliki-do-pobrania/otworz/{identifier}.pdf">{title}</a>'
        '<a class="downloads__item-action" '
        f'href="/pliki-do-pobrania/pobierz/{identifier}.pdf">Pobierz</a>'
        '<div class="downloads__item-details"><span>pdf</span><span> - </span>'
        "<span>265.24 KB</span></div></li>"
    )


def page(rows=None):
    return (
        '<main id="main-content"><h1>Działalność ochronna</h1>'
        '<section class="downloads"><h2>Dokumenty związane z działalnością ochronną</h2>'
        '<ul class="downloads__list">'
        + (row() if rows is None else rows)
        + '</ul></section><section class="text-with-links">'
        "<h2>Plan ochrony dla Ojcowskiego Parku Narodowego</h2>"
        f'<a class="text-with-links__item" href="{ISAP}">'
        '<div class="text-with-links__item__content">Pełna wersja planu ochrony</div>'
        "<svg><title>Idź do strony</title></svg></a></section></main>"
    ).encode()


class DocumentsParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_metadata_dates_unknown_no_body_or_legal_status(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (2, 0))
        for entry in entries:
            self.assertEqual((entry.published, entry.updated, entry.summary), ("", "", ""))
            self.assertFalse(entry.metadata["document_body_stored"])
            self.assertFalse(entry.metadata["legal_status_verified"])
        self.assertEqual(entries[0].metadata["source_file_size_text"], "pdf - 265.24 KB")
        self.assertEqual(entries[1].metadata["source_reference"], "WDU20210001432")

    def test_uuid_identity_not_title_or_position(self):
        first = self.perform()[0][0]
        changed = self.perform(page(row(title="Nowa nazwa")))[0][0]
        self.assertEqual(first.key, changed.key)
        self.assertNotEqual(first.digest(), changed.digest())
        other = row("586be6d7-5292-4ed1-a187-7b06642491c5")
        self.assertEqual(first.digest(), self.perform(page(other + row()))[0][1].digest())

    def test_schema_links_size_duplicate_and_budget_fail_closed(self):
        for body in [
            b"\xff",
            page(""),
            page(row() * 2),
            page(row("z037a270-59a2-44d1-92e3-9441b8460ada")),
            page().replace(b"/pobierz/", b"/otworz/"),
            page().replace(b"265.24 KB", b"unknown"),
            page().replace(b"WDU20210001432", b"WDU20210001433"),
            page().replace(b"main-content", b"challenge"),
            page().replace(b"</h1>", b"</h1><h1>Duplicate</h1>"),
            page().replace(b"</li>", b'<a href="https://evil.test">Other</a></li>'),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)
        rows = "".join(row(f"00000000-0000-0000-0000-{i:012d}") for i in range(101))
        with self.assertRaises(FetchError):
            self.perform(page(rows))

    def test_endpoint_profile_and_status_guards(self):
        for source, response in [
            (Source(adapter="web", url=NEWS_URL, config=CONFIG), Response(200, page(), {}, URL)),
            (Source(adapter="web", url=URL, config=NEWS_CONFIG), Response(200, page(), {}, URL)),
            (Source(adapter="web", url=URL, config=CONFIG), Response(403, page(), {}, URL)),
            (Source(adapter="web", url=URL, config=CONFIG), Response(200, page(), {}, NEWS_URL)),
        ]:
            with self.assertRaises(FetchError):
                parse(source, response)


class DocumentsConfigurationTests(TestCase):
    def test_catalogue_eligible_for_existing_scoped_activation(self):
        self.command(True)
        source = Source.objects.get(slug=SLUG)
        source.last_audit = {
            "status": "verified",
            "count": 4,
            "rejected": 0,
            "url": URL,
        }
        source.audited_at = timezone.now()
        source.save()
        with patch("ingestion.management.commands.activate_registry.batch_lock") as lock:
            lock.return_value.__enter__.return_value = True
            call_command("activate_registry", source=[SLUG], stdout=io.StringIO())
            source.refresh_from_db()
            self.assertFalse(source.enabled)
            call_command("activate_registry", source=[SLUG], apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        self.assertTrue(source.enabled)
        self.assertTrue(source.activation_approved)
        self.primary.refresh_from_db()
        self.assertEqual(self.primary.items.count(), 1)

    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.registry = RegistryEntry.objects.create(
            registry_id=151,
            release=release,
            organization="OPN",
            name="OPN",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.primary = Source.objects.create(
            slug="registry-151",
            name="News",
            adapter="web",
            url=NEWS_URL,
            config=NEWS_CONFIG,
            enabled=True,
            activation_approved=True,
            interval_minutes=360,
            last_audit={"status": "verified"},
        )
        self.registry.sources.add(self.primary)
        Item.objects.create(source=self.primary, external_id="old", title="Old", url=NEWS_URL)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_opn_documents.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_opn_documents", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserves_primary_and_no_http(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.assertFalse(Source.objects.filter(slug=SLUG).exists())
            self.command(True)
            s = Source.objects.get(slug=SLUG)
            self.assertEqual(
                (s.enabled, s.activation_approved, s.interval_minutes), (False, False, 1440)
            )
            s.enabled = s.activation_approved = True
            s.last_audit = {"status": "verified"}
            s.save()
            self.command(True)
            s.refresh_from_db()
            self.assertTrue(s.enabled)
            self.assertEqual(s.last_audit, {"status": "verified"})
        self.primary.refresh_from_db()
        self.assertTrue(self.primary.enabled)
        self.assertEqual(self.primary.items.count(), 1)
        self.assertEqual(self.primary.last_audit, {"status": "verified"})
        self.assertEqual(self.primary.interval_minutes, 360)

    def test_busy_and_administrator_guards(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        self.primary.config = {"admin": True}
        self.primary.save()
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertFalse(Source.objects.filter(slug=SLUG).exists())

    def test_unrelated_linkage_and_companion_configuration_preserved(self):
        self.command(True)
        s = Source.objects.get(slug=SLUG)
        s.config = {"admin": True}
        s.save()
        with self.assertRaises(CommandError):
            self.command(True)
        s.config = CONFIG
        s.save()
        self.registry.sources.add(Source.objects.create(slug="other", name="Other", url=URL))
        with self.assertRaises(CommandError):
            self.command(True)

    def test_ui_discloses_limits_and_escapes(self):
        entry = DocumentsParserTests().perform()[0][0]
        entry.metadata["source_file_size_text"] = "<script>alert(1)</script>"
        item = Item.objects.create(
            source=self.primary,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            metadata=entry.metadata,
            content_hash=entry.digest(),
        )
        response = self.client.get(f"/items/{item.pk}/")
        for label in [
            "pliku ani strony ISAP nie pobrano",
            "obowiązywania prawa nie zweryfikowano",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, label)
        self.assertNotContains(response, "<script>alert(1)</script>")
