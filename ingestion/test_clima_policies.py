import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.clima_policies import CONFIG, EXTERNAL_LINKS, GROUPS, LEGACY, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def card(
    slug="policy", title="Climate policy", description="Source description for 2050.", url=None
):
    return (
        '<article class="ecl-card"><div class="ecl-content-block__title">'
        f'<a href="{url or "/areas-action/" + slug + "_en"}">{title}</a></div>'
        f'<div class="ecl-content-block__description"><p>{description}</p></div></article>'
    )


def page(changes=None):
    changes = changes or {}
    sections = "".join(
        f'<div id="block-navigationeuaction{n}"><h2>{category}</h2>'
        + changes.get(n, card(slug=f"policy-{n}"))
        + "</div>"
        for n, category in enumerate(GROUPS, 1)
    )
    return (
        "<html><head><title>Areas of action - Climate Action - European Commission</title></head>"
        '<body><h1 class="ecl-page-header__title">Areas of action</h1>'
        '<main id="main-content">' + sections + "</main></body></html>"
    ).encode()


class ClimaParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_five_groups_descriptions_unknown_dates_and_no_legal_claim(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (5, 0))
        for entry, category in zip(entries, GROUPS, strict=True):
            self.assertEqual(entry.metadata["source_category"], category)
            self.assertEqual((entry.published, entry.updated), ("", ""))
            self.assertEqual(entry.summary, "Source description for 2050.")
            self.assertFalse(entry.metadata["current_legal_status_verified"])
            self.assertFalse(entry.metadata["target_page_downloaded"])
            self.assertFalse(entry.metadata["funding_call_confirmed"])
            self.assertNotIn("authors", entry.metadata)

    def test_fixed_external_links_are_metadata_only(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No child GET")):
            for url in EXTERNAL_LINKS:
                entry = self.perform(page({1: card(url=url)}))[0][0]
                self.assertEqual(entry.url, url)
                self.assertFalse(entry.metadata["target_page_downloaded"])

    def test_identity_omits_title_and_order_but_observes_description_changes(self):
        original = self.perform()[0][0]
        changed = self.perform(page({1: card(slug="policy-1", title="Changed")}))[0][0]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())
        changed = self.perform(page({1: card(slug="policy-1", description="New source text")}))[0][
            0
        ]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())
        moved = self.perform(page({1: card(slug="other") + card(slug="policy-1")}))[0][1]
        self.assertEqual(original.digest(), moved.digest())

    def test_page_structure_encoding_missing_groups_and_unknown_cards_fail_closed(self):
        for old, new in [
            ("Areas of action - Climate Action - European Commission", "Challenge"),
            ('id="main-content"', 'id="other"'),
            ('id="block-navigationeuaction1"', 'id="other"'),
            ('class="ecl-page-header__title"', 'class="other"'),
            ("<h2>Mitigation</h2>", "<h2>Unknown</h2>"),
            ('class="ecl-content-block__description"', 'class="other"'),
            ("</main>", card(slug="unassigned") + "</main>"),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page({1: ""})]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_link_and_description_guards_duplicates_and_budgets(self):
        for url in [
            "http://climate.ec.europa.eu/areas-action/x_en",
            "https://example.com/areas-action/x_en",
            "/areas-action/x_en?download=1",
            "/areas-action/x_en#target",
            "https://climate-pact.europa.eu/unreviewed_en",
        ]:
            with self.subTest(url=url), self.assertRaises(FetchError):
                self.perform(page({1: card(url=url)}))
        for changes in [
            {1: card(description="")},
            {1: card(description="x" * 601)},
            {1: card(title="")},
            {1: card(slug="policy-2")},
            {1: "".join(card(slug=f"many-{n}") for n in range(51))},
            {n: "".join(card(slug=f"policy-{n}-{i}") for i in range(31)) for n in range(1, 6)},
        ]:
            with self.subTest(keys=list(changes)), self.assertRaises(FetchError):
                self.perform(page(changes))

    def test_relative_absolute_same_identity_profile_and_response_guards(self):
        original = self.perform()[0][0]
        absolute = self.perform(
            page({1: card(url="https://climate.ec.europa.eu/areas-action/policy-1_en")})
        )[0][0]
        self.assertEqual(original.digest(), absolute.digest())
        for config, status, url in [
            ({**CONFIG, "admin": True}, 200, URL),
            (CONFIG, 304, URL),
            (CONFIG, 200, LEGACY),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=URL, config=config), Response(status, page(), {}, url)
                )


class ClimaConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=167,
            release=release,
            organization="DG CLIMA",
            name="Policies",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-167",
            name="Policies",
            adapter="web",
            url=LEGACY,
            config={},
            enabled=False,
            activation_approved=False,
            interval_minutes=240,
            scope="Lista rozpoznana przez parser lub jawny monitoring tekstu strony. "
            "Bez pełnego archiwum.",
            etag="old",
            last_audit={"status": "unverified"},
        )
        self.entry.sources.add(self.source)

    def command(self, apply=False, acquired=True):
        with patch("ingestion.management.commands.configure_clima_policies.batch_lock") as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_clima_policies", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_preserve_identity_admin_audit_and_no_http(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            self.command()
            self.source.refresh_from_db()
            self.assertEqual(self.source.url, LEGACY)
            pk = self.source.pk
            self.command(True)
            self.source.refresh_from_db()
            self.assertEqual(
                (self.source.pk, self.source.url, self.source.config), (pk, URL, CONFIG)
            )
            self.assertEqual(self.source.interval_minutes, 240)
            self.assertEqual(self.source.last_audit, {})
            self.source.enabled = self.source.activation_approved = True
            self.source.last_audit = {"status": "verified"}
            self.source.save()
            self.command(True)
            self.source.refresh_from_db()
            self.assertTrue(self.source.enabled)
            self.assertEqual(self.source.last_audit, {"status": "verified"})

    def test_busy_admin_history_and_linkage_guards(self):
        with self.assertRaises(CommandError):
            self.command(True, False)
        for field, value in [
            ("enabled", True),
            ("activation_approved", True),
            ("failures", 1),
            ("interval_minutes", 60),
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

    def test_existing_item_history_is_preserved(self):
        Item.objects.create(source=self.source, external_id="history", title="History", url=LEGACY)
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(self.source.items.count(), 1)

    def test_ui_discloses_source_description_unknown_dates_and_escapes(self):
        entry = ClimaParserTests().perform()[0][0]
        entry.metadata["source_category"] = "<script>alert(1)</script>"
        item = Item.objects.create(
            source=self.source,
            external_id=entry.key,
            title=entry.title,
            url=entry.url,
            summary=entry.summary,
            metadata=entry.metadata,
            content_hash=entry.digest(),
        )
        response = self.client.get(f"/items/{item.pk}/")
        self.assertEqual(response.status_code, 200)
        for value in [
            "Opis z karty",
            "nie podaje dat",
            "Strony docelowej",
            "niezależną weryfikacją",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
