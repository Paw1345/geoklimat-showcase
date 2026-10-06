import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.environment_policies import CONFIG, EXTERNAL_LINK, GROUPS, LEGACY, URL
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.transport import FetchError, Response


def topic(slug="topic", title="Environmental topic", url=None):
    return (
        '<li class="ecl-content-block__link-item">'
        f'<a href="{url or "/topics/" + slug + "_en"}">{title}</a></li>'
    )


def page(changes=None, description=""):
    changes = changes or {}
    cards = "".join(
        '<article class="ecl-navigation-list__item">'
        f'<div class="ecl-content-block__title"><a href="{path}">{category}</a></div>'
        f'<div class="ecl-content-block__description">{description}</div>'
        '<ul class="ecl-content-block__link-list">'
        + changes.get(category, topic(slug=f"topic-{n}"))
        + "</ul></article>"
        for n, (category, path) in enumerate(GROUPS.items())
    )
    return (
        "<html><head><title>Environment - Environment - European Commission</title></head>"
        '<body><h1 class="ecl-page-header__title">Environment</h1>'
        '<main id="main-content">' + cards + "</main></body></html>"
    ).encode()


class EnvironmentParserTests(SimpleTestCase):
    def perform(self, body=None):
        return parse(
            Source(adapter="web", url=URL, config=CONFIG),
            Response(200, page() if body is None else body, {}, URL),
        )

    def test_three_groups_topics_unknown_dates_no_invented_descriptions(self):
        entries, rejected = self.perform()
        self.assertEqual((len(entries), rejected), (6, 0))
        self.assertEqual(
            [e.metadata["source_index_role"] for e in entries],
            ["group", "topic", "group", "topic", "group", "topic"],
        )
        for entry in entries:
            self.assertEqual((entry.published, entry.updated, entry.summary), ("", "", ""))
            self.assertFalse(entry.metadata["current_legal_status_verified"])
            self.assertFalse(entry.metadata["target_page_downloaded"])
            self.assertFalse(entry.metadata["funding_call_confirmed"])
            self.assertNotIn("authors", entry.metadata)

    def test_group_description_is_not_topic_description_or_date(self):
        entries = self.perform(page(description="Strategy goals for 2030."))[0]
        self.assertEqual(entries[0].summary, "Strategy goals for 2030.")
        self.assertEqual(entries[1].summary, "")
        self.assertEqual(entries[0].published, "")

    def test_fixed_external_link_does_not_fetch_target(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No child GET")):
            entry = self.perform(page({"Circular economy": topic(url=EXTERNAL_LINK)}))[0][1]
        self.assertEqual(entry.url, EXTERNAL_LINK)
        self.assertFalse(entry.metadata["target_page_downloaded"])

    def test_identity_omits_title_order_and_relative_absolute_representation(self):
        original = self.perform()[0][1]
        changed = self.perform(page({"Circular economy": topic(slug="topic-0", title="Changed")}))[
            0
        ][1]
        self.assertEqual(original.key, changed.key)
        self.assertNotEqual(original.digest(), changed.digest())
        absolute = self.perform(
            page(
                {
                    "Circular economy": topic(
                        url="https://environment.ec.europa.eu/topics/topic-0_en"
                    )
                }
            )
        )[0][1]
        self.assertEqual(original.digest(), absolute.digest())
        moved = self.perform(page({"Circular economy": topic(slug="new") + topic(slug="topic-0")}))[
            0
        ][2]
        self.assertEqual(original.digest(), moved.digest())

    def test_page_schema_encoding_and_missing_group_fail_closed(self):
        for old, new in [
            ("Environment - Environment - European Commission", "Challenge"),
            ('id="main-content"', 'id="other"'),
            ('class="ecl-page-header__title"', 'class="other"'),
            ("Circular economy", "Other group"),
            ("/strategy/circular-economy_en", "/strategy/other_en"),
            ('class="ecl-content-block__description"', 'class="other"'),
            ('class="ecl-content-block__link-list"', 'class="other"'),
        ]:
            with self.subTest(old=old), self.assertRaises(FetchError):
                self.perform(page().replace(old.encode(), new.encode()))
        for body in [b"\xff", b"<html>Challenge</html>", page({"Circular economy": ""})]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_identity_unknown_links_duplicates_and_budgets_fail_closed(self):
        for url in [
            "https://example.com/topics/x_en",
            "http://environment.ec.europa.eu/topics/x_en",
            "/topics/x_en?download=1",
            "/topics/x_en#target",
            "https://commission.europa.eu/unreviewed_en",
            "/news/x_en",
        ]:
            with self.subTest(url=url), self.assertRaises(FetchError):
                self.perform(page({"Circular economy": topic(url=url)}))
        for body in [
            page({"Circular economy": topic(slug="topic-1")}),
            page({"Circular economy": topic() + topic()}),
            page({"Circular economy": topic(title="")}),
            page(description="x" * 601),
            page({"Circular economy": topic() + "<li>Unknown</li>"}),
            page({"Circular economy": "".join(topic(slug=f"many-{n}") for n in range(51))}),
            page(
                {
                    g: "".join(topic(slug=f"many-{i}-{n}") for n in range(50))
                    for i, g in enumerate(GROUPS)
                }
            ),
        ]:
            with self.assertRaises(FetchError):
                self.perform(body)

    def test_profile_status_and_response_url_guards(self):
        for config, status, url in [
            ({**CONFIG, "admin": True}, 200, URL),
            (CONFIG, 304, URL),
            (CONFIG, 200, LEGACY),
        ]:
            with self.assertRaises(FetchError):
                parse(
                    Source(adapter="web", url=URL, config=config), Response(status, page(), {}, url)
                )


class EnvironmentConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(
            filename="fixture.xlsx", sha256="a" * 64, sheets={}
        )
        self.entry = RegistryEntry.objects.create(
            registry_id=165,
            release=release,
            organization="DG ENV",
            name="Policies",
            priority="P1",
            technical_status="VERIFY_ENDPOINT",
            integration_state="configured",
            master={},
            technical={},
        )
        self.source = Source.objects.create(
            slug="registry-165",
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
        with patch(
            "ingestion.management.commands.configure_environment_policies.batch_lock"
        ) as lock:
            lock.return_value.__enter__.return_value = acquired
            call_command("configure_environment_policies", apply=apply, stdout=io.StringIO())

    def test_dry_apply_repeat_no_http_preserve_identity_audit(self):
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

    def test_busy_admin_history_linkage_guards(self):
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

    def test_existing_history_is_preserved(self):
        Item.objects.create(source=self.source, external_id="history", title="History", url=LEGACY)
        with self.assertRaises(CommandError):
            self.command(True)
        self.assertEqual(self.source.items.count(), 1)

    def test_ui_discloses_topic_scope_unknown_dates_and_escapes(self):
        entry = EnvironmentParserTests().perform()[0][1]
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
            "Temat w grupie",
            "nie podaje dat",
            "Strony docelowej",
            "nie obejmuje wszystkich wiadomości",
            "&lt;script&gt;",
        ]:
            self.assertContains(response, value)
        self.assertNotContains(response, "<script>alert(1)</script>")
