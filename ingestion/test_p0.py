import io
import json
from unittest.mock import Mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from ingestion.adapters import parse
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.p0_profiles import PREVIOUS_URLS, PROFILES
from ingestion.publications import enrich
from ingestion.service import fetch_entries, ingest_source
from ingestion.transport import FetchError, Response


def response(html, url):
    return Response(200, html.encode(), {}, url)


def funding_html(end="2027-04-30", contact="Person phone email@example.test"):
    header = (
        '<tr><th colspan="2">Nr PP</th><th>Nazwa programu / priorytetu</th>'
        "<th>Tryb naboru</th><th>Planowana data rozpoczęcia naboru wniosków</th>"
        "<th>Planowana data zakończenia naboru wniosków</th><th>Stan naboru</th>"
        "<th>Oznaczenie naboru</th><th>Koordynator</th><th>Uwagi</th></tr>"
    )
    rows = (
        '<tr><td colspan="2">1</td><td colspan="8">Transformacja</td></tr>'
        '<tr><td colspan="2" rowspan="2">1.1</td><td>OZE A</td><td>ciągły</td>'
        f"<td>III/IV kwartał 2026</td><td>{end}</td><td>planowany</td>"
        f"<td>CALL-A</td><td>{contact}</td><td>not stored</td></tr>"
        "<tr><td>OZE B</td><td>ciągły</td><td>2026-10-01</td><td>2027-01-31</td>"
        f"<td>trwa nabór</td><td>CALL-B</td><td>{contact}</td><td></td></tr>"
        "<tr>" + "<td></td>" * 10 + "</tr>"
    )
    return "<main><table>" + header + rows + "</table></main>"


def document_page(key, next_link=""):
    html = (
        '<div class="ecl-content-item"><h2 class="ecl-content-block__title">'
        f'<a href="/document/{key}">Document {key}</a></h2>'
        '<span class="ecl-content-block__primary-meta-item">31 July 2024</span></div>'
    )
    if next_link:
        html += f'<li class="ecl-pagination__item--next"><a href="{next_link}">Next</a></li>'
    return html


class P0ParserTests(TestCase):
    def source(self, key, save=False):
        url, config, note = PROFILES[key]
        source = Source(
            slug=f"registry-{key:03d}", name=note, url=url, adapter="web", config=config.copy()
        )
        if save:
            source.enabled = source.activation_approved = True
            source.save()
        return source

    def test_funding_spans_dates_and_no_contacts(self):
        source = self.source(31)
        entries, bad = parse(source, response(funding_html(), source.url))
        self.assertEqual((len(entries), bad), (2, 0))
        self.assertEqual(entries[1].metadata["funding"]["program_code"], "1.1")
        self.assertEqual(entries[0].metadata["funding"]["start_text"], "III/IV kwartał 2026")
        self.assertEqual(entries[0].published, "")
        self.assertNotIn("Person", json.dumps(entries[0].metadata))
        self.assertNotIn("not stored", json.dumps(entries[0].metadata))
        changed, _ = parse(source, response(funding_html(contact="Changed person"), source.url))
        self.assertEqual(entries[0].digest(), changed[0].digest())

    def test_funding_history_and_publication_identity(self):
        source = self.source(31, save=True)
        client = Mock()
        client.get.return_value = response(funding_html(), source.url)
        self.assertEqual(ingest_source(source, client).created, 2)
        self.assertEqual(ingest_source(source, client).unchanged, 2)
        left, right = list(Item.objects.order_by("pk"))
        self.assertNotEqual(left.publication_id, right.publication_id)
        self.assertContains(self.client.get("/?source=" + source.slug), "Nabór: OZE A")
        self.assertContains(self.client.get("/?source=" + source.slug), "Nabór: OZE B")
        client.get.return_value = response(funding_html(end="2027-05-31"), source.url)
        run = ingest_source(source, client)
        self.assertEqual((run.created, run.updated, run.unchanged), (0, 1, 1))
        self.assertEqual(left.versions.count(), 2)
        # Normal pages sharing the schedule URL must not absorb funding calls.
        for key in ("page1", "page2"):
            ordinary = Item.objects.create(
                source=source, external_id=key, url=source.url, title="Schedule"
            )
            enrich(ordinary)
            self.assertNotEqual(ordinary.publication_id, left.publication_id)
        self.assertEqual(
            Item.objects.get(external_id="page1").publication_id, ordinary.publication_id
        )

    def test_funding_schema_and_ambiguous_identity_rejected(self):
        source = self.source(31)
        for html in (
            funding_html().replace("Nazwa programu", "Changed header"),
            funding_html().replace('rowspan="2"', 'rowspan="1001"'),
            funding_html().replace("OZE B", "OZE A").replace("CALL-B", "CALL-A"),
            "<main>" + "Readable content " * 30 + "</main>",
        ):
            with self.subTest(html=html[:80]), self.assertRaises(FetchError):
                parse(source, response(html, source.url))

    def test_wordpress_header_and_service_kind(self):
        html = (
            '<main><article class="post"><header><h2 class="entry-title">'
            '<a href="/news">News</a></h2><time datetime="2026-09-18">Today</time>'
            "</header></article></main>"
        )
        source = self.source(10)
        entries, _ = parse(source, response(html, source.url))
        self.assertEqual((entries[0].title, entries[0].published), ("News", "2026-09-18"))
        source.config = {}  # Generic parser must also retain article headers.
        self.assertEqual(parse(source, response(html, source.url))[0][0].title, "News")
        source = self.source(11)
        html = '<main><li class="wp-block-post"><h2><a href="/wfs">WFS</a></h2></li></main>'
        self.assertEqual(
            parse(source, response(html, source.url))[0][0].metadata["record_kind"],
            "service_directory",
        )

    def test_kobize_uses_publication_label_not_title_date(self):
        source = self.source(28)
        html = (
            '<div id="right-column"><article><header><h4><a href="/news">'
            "Ustawa 31.07.2026</a></h4>Data publikacji: 18.09.2026</header></article></div>"
        )
        entry = parse(source, response(html, source.url))[0][0]
        self.assertEqual(entry.published, "18.09.2026")

    def test_ekoportal_full_title_and_polish_date(self):
        source = self.source(17)
        html = (
            '<a class="inv-news-list-link" href="/news" title="Full title">'
            '<div class="news-tabletype"><span>21</span><span>lipiec</span><span>2026</span>'
            '</div><h3 class="news-info-teaser">Truncated…</h3></a>'
        )
        entry = parse(source, response(html, source.url))[0][0]
        self.assertEqual((entry.title, entry.published), ("Full title", "2026-07-21"))

    def test_all_profiles_fail_closed_without_rows(self):
        for key in PROFILES:
            source = self.source(key)
            with self.subTest(key=key), self.assertRaises(FetchError):
                parse(source, response("<main>" + "Readable " * 50 + "</main>", source.url))

    def test_category_notices_keep_distinct_identity_and_repair_history(self):
        source = self.source(17, save=True)
        html = "".join(
            '<a class="inv-news-list-link" href="/category" title="Notice">'
            f'<div class="news-tabletype"><span>{day}</span><span>lipiec</span>'
            '<span>2026</span></div><h3 class="news-info-teaser">Notice</h3></a>'
            for day in (20, 21)
        )
        entries, _ = parse(source, response(html, source.url))
        self.assertNotEqual(entries[0].key, entries[1].key)
        import hashlib

        old = Item.objects.create(
            source=source,
            external_id=hashlib.sha256(entries[0].url.encode()).hexdigest(),
            title="Notice",
            url=entries[0].url,
            metadata={"publication_date_raw": entries[0].published},
        )
        enrich(old)
        call_command("repair_ekoportal_identities", stdout=io.StringIO())
        old.refresh_from_db()
        self.assertNotEqual(old.external_id, entries[0].key)
        call_command("repair_ekoportal_identities", apply=True, stdout=io.StringIO())
        call_command("repair_ekoportal_identities", apply=True, stdout=io.StringIO())
        old.refresh_from_db()
        self.assertEqual(old.external_id, entries[0].key)
        self.assertEqual(old.versions.count(), 1)
        # Repair deliberately revokes approval; import requires a new approved fixture.
        source.refresh_from_db()
        source.enabled = source.activation_approved = True
        source.save(update_fields=["enabled", "activation_approved"])
        client = Mock()
        client.get.return_value = response(html, source.url)
        self.assertEqual(ingest_source(source, client).created, 1)
        self.assertEqual(Item.objects.values("publication_id").distinct().count(), 2)
        self.assertEqual(ingest_source(source, client).unchanged, 2)

    def test_html_pagination_eleven_pages(self):
        source = self.source(179)
        client = Mock()
        client.get.side_effect = [
            response(
                document_page(str(i), f"?page={i + 1}" if i < 10 else ""),
                source.url + (f"?page={i}" if i else ""),
            )
            for i in range(11)
        ]
        entries, bad, _, _ = fetch_entries(source, client)
        self.assertEqual((len(entries), bad), (11, 0))
        self.assertEqual(entries[0].published, "2024-07-31")
        self.assertEqual(entries[0].metadata["record_kind"], "technical_document")
        self.assertEqual(client.get.call_count, 11)

    def test_html_pagination_rejects_external_links_and_loops(self):
        source = self.source(179)
        for link in (
            "https://bad.example/",
            "/other",
            source.url,
            "http://knowledge-base.inspire.ec.europa.eu/tools/document-library_en",
        ):
            client = Mock()
            client.get.return_value = response(document_page("one", link), source.url)
            with self.subTest(link=link), self.assertRaises(FetchError):
                fetch_entries(source, client)
            self.assertEqual(client.get.call_count, 1)

    def test_html_pagination_budget_commits_nothing(self):
        source = self.source(179, save=True)
        source.config["max_pages"] = 1
        source.save()
        client = Mock()
        client.get.return_value = response(document_page("one", "?page=1"), source.url)
        self.assertEqual(ingest_source(source, client).status, "failed")
        self.assertFalse(Item.objects.exists())

    def test_html_pagination_repeated_record_and_redirect_rejected(self):
        source = self.source(179)
        client = Mock()
        client.get.side_effect = [
            response(document_page("one", "?page=1"), source.url),
            response(document_page("one"), source.url + "?page=1"),
        ]
        with self.assertRaises(FetchError):
            fetch_entries(source, client)
        client.get.side_effect = None
        client.get.return_value = response(document_page("one"), source.url + "/other")
        with self.assertRaises(FetchError):
            fetch_entries(source, client)


class P0ConfigurationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        for key in PROFILES:
            RegistryEntry.objects.create(
                registry_id=key,
                release=release,
                priority="P0",
                country="Polska",
                master={"Poll interval (min)": 60},
                technical={},
            )

    def test_dry_run_apply_idempotence_and_original_preserved(self):
        out = io.StringIO()
        call_command("configure_p0", stdout=out)
        self.assertEqual(Source.objects.count(), 0)
        call_command("configure_p0", apply=True, stdout=out)
        self.assertEqual(Source.objects.count(), 6)
        source = Source.objects.get(slug="registry-010")
        source.enabled = source.activation_approved = True
        source.last_audit = {"status": "verified"}
        source.save()
        call_command("configure_p0", apply=True, stdout=out)
        source.refresh_from_db()
        self.assertTrue(source.enabled and source.activation_approved)
        self.assertEqual(source.last_audit["status"], "verified")
        self.assertEqual(
            RegistryEntry.objects.get(registry_id=10).master, {"Poll interval (min)": 60}
        )

    def test_upgrade_keeps_history_revokes_audit_and_skips_admin_changes(self):
        for key in (17, 28):
            source = Source.objects.create(
                slug=f"registry-{key:03d}",
                adapter="web",
                url=PREVIOUS_URLS[key],
                config={} if key == 17 else {"edited": True},
                enabled=True,
                activation_approved=True,
                audited_at=timezone.now(),
                last_audit={"status": "verified"},
                etag="old",
            )
            RegistryEntry.objects.get(registry_id=key).sources.add(source)
            Item.objects.create(source=source, external_id="old", title="History", url=source.url)
        call_command("configure_p0", apply=True, stdout=io.StringIO())
        upgraded = Source.objects.get(slug="registry-017")
        self.assertFalse(upgraded.enabled or upgraded.activation_approved)
        self.assertIsNone(upgraded.audited_at)
        self.assertEqual((upgraded.last_audit, upgraded.etag), ({}, ""))
        self.assertEqual(Item.objects.count(), 2)
        edited = Source.objects.get(slug="registry-028")
        self.assertEqual(edited.config, {"edited": True})
        self.assertTrue(edited.enabled)
