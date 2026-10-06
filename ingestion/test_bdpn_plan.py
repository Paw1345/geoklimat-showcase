import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.bdpn_news import CONFIG as NEWS_CONFIG
from ingestion.bdpn_news import URL as NEWS_URL
from ingestion.bdpn_plan import CONFIG, GROUP, ISAP, SLUG, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def metric(values):
    return "".join(
        f'<div class="bip-accordion__item__content__item"><div>{k}</div><div>{v}</div></div>'
        for k, v in values
    )


def card(identifier="680b8611be257851338664", extension="pdf", stamp="25.04.2025 14:50:10"):
    kind, label = ("PDF", "PDF") if extension == "pdf" else ("ZIP", "SHP")
    return (
        '<div class="bip-attachments__container__item">'
        '<a class="bip-attachments__container__item__title" '
        f'href="/uploads/files/{identifier}.{extension}">W formacie {label}</a>'
        f'<div class="bip-attachments__container__item__size">{kind} - 12.81 MB</div>'
        '<div class="bip-accordion__item__content">'
        + metric(
            [
                ("Podmiot udostępniający informację:", "Bieszczadzki Park Narodowy"),
                ("Czas publikacji:", stamp),
            ]
        )
        + "</div></div>"
    )


def page(cards=None, visitors="2976"):
    return (
        '<main id="main-content"><h1>Plan ochrony - BIP Bieszczadzki Park Narodowy</h1>'
        '<section class="bip-block-content">'
        '<span class="bip-title__content__title">Plan ochrony</span>'
        f'<div class="bip__text"><a href="{ISAP}">Rozporządzenie 2022</a></div>'
        f'<div class="bip-attachments"><h2>{GROUP}</h2>'
        '<div class="bip-attachments__container">'
        + (
            card() + card("680b8611c05ce874720053", "zip", "25.04.2025 14:50:42")
            if cards is None
            else cards
        )
        + '</div></div><div class="bip-accordion"><div class="bip-accordion__item">'
        '<div id="bip-accordion-item-0">'
        + metric(
            [
                ("Liczba odwiedzin:", visitors),
                ("Podmiot udostępniający informację:", "Bieszczadzki Park Narodowy"),
                ("Data publikacji:", "25.04.2025"),
                ("Data pierwotnej publikacji:", "22.11.2023 22:54:13"),
                ("Data ostatniej modyfikacji:", "25.04.2025 14:54:41"),
            ]
        )
        + "</div></div></div></section></main>"
    ).encode()


class PlanParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_source_days_distinct_from_act_map_and_original_dates(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (3, 0))
        for entry in entries:
            self.assertEqual((entry.published, entry.updated), ("2025-04-25", ""))
            self.assertFalse(entry.metadata["legal_status_verified"])
            self.assertFalse(entry.metadata["document_body_stored"])
            self.assertTrue(entry.metadata["source_timezone_unknown"])
        self.assertEqual(
            entries[0].metadata["source_original_publication_text"], "22.11.2023 22:54:13"
        )
        self.assertEqual(entries[2].metadata["source_document_type"], "SHP (ZIP)")
        self.assertEqual(entries[0].metadata["source_reference_url"], ISAP)

    def test_counters_order_and_title_do_not_change_identity(self):
        first = self.perform()[0]
        self.assertEqual(
            [e.digest() for e in first],
            [e.digest() for e in self.perform(page(visitors="3000"))[0]],
        )
        moved = self.perform(
            page(card("680b8611c05ce874720053", "zip", "25.04.2025 14:50:42") + card())
        )[0]
        self.assertEqual(first[1].digest(), moved[2].digest())

    def test_invalid_dates_schema_reference_duplicates_fail_closed(self):
        for body in [
            b"\\xff",
            page(""),
            page(card() * 2),
            page(card(stamp="31.02.2025 14:50:10")),
            page(card(stamp="25.04.2025 99:50:10")),
            page().replace(b"WDU20220001919", b"WDU20220001920"),
            page().replace(b"25.04.2025", b"25/04/2025"),
            page().replace(b"22.11.2023", b"22.11.2027"),
            page().replace(b"12.81 MB", b"12.81 bytes"),
            page().replace(b"W formacie SHP", b"W formacie PDF"),
            page().replace(b"main-content", b"challenge"),
            page().replace(b"</h1>", b"</h1><h1>Duplicate</h1>"),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_endpoint_status_and_profile_guards(self):
        for source, response in [
            (Source(adapter="web", url=NEWS_URL, config=CONFIG), Response(200, page(), {}, URL)),
            (Source(adapter="web", url=URL, config=NEWS_CONFIG), Response(200, page(), {}, URL)),
            (Source(adapter="web", url=URL, config=CONFIG), Response(403, page(), {}, URL)),
        ]:
            with self.assertRaises(FetchError):
                parse(source, response)


class DocumentsConfigurationTests(TestCase):
    def test_catalogue_eligible_for_existing_scoped_activation(self):
        self.command(True)
        source = Source.objects.get(slug=SLUG)
        source.last_audit = {
            "status": "verified",
            "count": 3,
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
            registry_id=144,
            release=release,
            organization="BDPN",
            name="BDPN",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.primary = Source.objects.create(
            slug="registry-144",
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
        with patch("ingestion.management.commands.configure_bdpn_plan.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_bdpn_plan", apply=apply, stdout=io.StringIO())

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

    def test_ui_distinguishes_dates_and_escapes(self):
        entry = PlanParserTests().perform()[0][1]
        entry.metadata["source_file_size_text"] = "<script>alert(1)</script>"
        item = Item.objects.create(
            source=self.primary,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            metadata=entry.metadata,
        )
        response = self.client.get(f"/items/{item.pk}/")
        for value in ["Data strony, załącznika, aktu i stan mapy", "strefy", "&lt;script&gt;"]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
