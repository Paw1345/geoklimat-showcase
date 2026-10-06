import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.robotparser import RobotFileParser

from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from ingestion.adapters import Entry, parse, safe_entry
from ingestion.catalog import GOV_CONFIG
from ingestion.models import IngestionRun, Item, Source
from ingestion.service import fetch_entries, ingest_source, run_batch
from ingestion.transport import Client, FetchError, Response, retry_delay, validate_url

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
<link>https://www.gov.pl/</link><description>Test</description><item><guid>test-1</guid>
<title>Zmiana API</title><link>https://www.gov.pl/web/gios/test</link>
<pubDate>Thu, 17 Sep 2026 10:00:00 GMT</pubDate><description>Komunikat</description>
</item></channel></rss>"""


def response(body=RSS, status=200, headers=None, url="https://www.gov.pl/web/gios/feed"):
    return Response(status, body, headers or {}, url)


class AdapterTests(SimpleTestCase):
    def test_real_pdf_bytes(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 200 Td (PDF test document) Tj ET")
        page[NameObject("/Contents")] = stream
        buffer = io.BytesIO()
        writer.write(buffer)
        entries, _ = parse(
            Source(adapter="pdf", url="https://www.gov.pl/test.pdf", name="PDF"),
            response(buffer.getvalue()),
        )
        self.assertIn("PDF test document", entries[0].summary)

    def test_rss_and_atom(self):
        source = Source(adapter="feed")
        entries, rejected = parse(source, response())
        self.assertEqual((entries[0].title, rejected), ("Zmiana API", 0))
        atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title>
        <entry><id>test2</id><title>Atom</title><link href="https://www.gov.pl/test"/>
        <updated>2026-09-17T10:00:00Z</updated></entry></feed>"""
        self.assertEqual(parse(source, response(atom))[0][0].title, "Atom")

    def test_gov_html_and_missing_layout(self):
        source = Source(adapter="html", config=GOV_CONFIG)
        body = (
            b'<div class="art-prev"><li><div class="title">'
            b'<a href="/web/gios/a">A</a></div>'
            b'<span class="date">17.09.2026</span></li></div>'
        )
        entries, rejected = parse(source, response(body))
        self.assertEqual(entries[0].url, "https://www.gov.pl/web/gios/a")
        self.assertEqual(entries[0].published, "17.09.2026")
        self.assertEqual(rejected, 0)
        with self.assertRaises(FetchError):
            parse(source, response(b"<html>changed</html>"))

    def test_eli_metadata_and_incomplete_page(self):
        data = {
            "count": 1,
            "items": [
                {
                    "ELI": "DU/2026/1",
                    "title": "Akt",
                    "promulgation": "2026-01-01",
                    "status": "obowiązujący",
                }
            ],
        }
        entries, _ = parse(Source(adapter="eli"), response(json.dumps(data).encode()))
        self.assertEqual(entries[0].metadata["ELI"], "DU/2026/1")
        data["count"] = 2
        with self.assertRaises(FetchError):
            parse(Source(adapter="eli"), response(json.dumps(data).encode()))

    def test_pdf_text_extraction_and_empty_scan(self):
        reader = Mock(is_encrypted=False, pages=[Mock()])
        reader.pages[0].extract_text.return_value = "Dokument testowy"
        with patch("ingestion.adapters.PdfReader", return_value=reader):
            entries, _ = parse(
                Source(adapter="pdf", name="PDF", url="https://www.gov.pl/a.pdf"),
                response(b"%PDF mock"),
            )
            self.assertEqual(entries[0].summary, "Dokument testowy")
            reader.pages[0].extract_text.return_value = ""
            with self.assertRaises(FetchError):
                parse(Source(adapter="pdf"), response(b"%PDF mock"))

    def test_invalid_feed_and_links(self):
        with self.assertRaises(FetchError):
            parse(Source(adapter="feed"), response(b"<html>Error</html>"))
        self.assertIsNone(
            safe_entry(Entry("id", "title", "javascript:alert(1)"), "https://www.gov.pl")
        )
        self.assertIsNone(safe_entry(Entry("id", "title", ""), "https://www.gov.pl"))

    def test_watch_hash_detects_change_beyond_excerpt(self):
        source = Source(
            adapter="watch", url="https://www.gov.pl/test", name="Test", config={"content": "main"}
        )
        first = parse(source, response(("<main>" + "a " * 600 + "x</main>").encode()))[0][0]
        second = parse(source, response(("<main>" + "a " * 600 + "y</main>").encode()))[0][0]
        self.assertEqual(first.summary, second.summary)
        self.assertNotEqual(first.digest(), second.digest())


class PipelineTests(TransactionTestCase):
    def test_worker_runs_data_before_bounded_news_batch(self):
        order = []

        def collect_data():
            order.append("data")
            return []

        def collect_news(*, limit):
            order.append(("news", limit))
            return []

        with (
            patch("ingestion.management.commands.ingestion_worker.call_command"),
            patch("ingestion.management.commands.ingestion_worker.run_downloads", return_value=[]),
            patch(
                "ingestion.management.commands.ingestion_worker.run_data", side_effect=collect_data
            ),
            patch(
                "ingestion.management.commands.ingestion_worker.run_batch", side_effect=collect_news
            ),
        ):
            call_command("ingestion_worker", once=True)
        self.assertEqual(order, ["data", ("news", 5)])

    def test_limited_batch_keeps_other_due_sources_for_next_cycle(self):
        self.source.enabled = False
        self.source.save(update_fields=["enabled"])
        other = Source.objects.create(
            slug="due-second",
            name="Second",
            organization="Test",
            adapter="feed",
            url="https://www.gov.pl/web/gios/feed",
            enabled=True,
            activation_approved=True,
            next_fetch_at=timezone.now(),
        )
        first = Source.objects.create(
            slug="due-first",
            name="First",
            organization="Test",
            adapter="feed",
            url="https://www.gov.pl/web/gios/feed",
            enabled=True,
            activation_approved=True,
            next_fetch_at=timezone.now() - timedelta(hours=1),
        )
        with patch("ingestion.service.ingest_source") as ingest:
            ingest.return_value = Mock(
                status="unchanged", fetched=0, created=0, updated=0, unchanged=0, error=""
            )
            result = run_batch(limit=1)
        self.assertEqual([row["source"] for row in result], [first.slug])
        ingest.assert_called_once()
        other.refresh_from_db()
        self.assertTrue(other.enabled)

    def test_parallel_batch_does_not_ingest(self):
        other = connection.copy(alias="lock_test")
        try:
            with other.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(726042)")
            with patch("ingestion.service.Client") as client:
                self.assertEqual(run_batch(force=True), [{"status": "busy"}])
                client.assert_not_called()
            self.assertEqual(IngestionRun.objects.count(), 0)
        finally:
            other.close()

    def test_worker_once_with_no_due_sources(self):
        self.source.enabled = False
        self.source.save()
        with patch("ingestion.service.Client") as client:
            call_command("ingestion_worker", once=True)
            client.return_value.get.assert_not_called()

    def test_partial_feed_records_rejections(self):
        broken = b'<item><guid isPermaLink="false">bad</guid><title>Missing link</title></item>'
        self.client_http.get.return_value = response(
            RSS.replace(b"</channel>", broken + b"</channel>")
        )
        run = ingest_source(self.source, self.client_http)
        self.assertEqual((run.status, run.rejected, run.created), ("partial", 1, 1))

    def setUp(self):
        self.source = Source.objects.create(
            slug="test",
            name="Test",
            organization="Test",
            adapter="feed",
            url="https://www.gov.pl/web/gios/feed",
            enabled=True,
        )
        self.client_http = Mock()
        self.client_http.get.return_value = response(headers={"etag": '"v1"'})

    def test_idempotency_update_and_first_seen_preserved(self):
        first = ingest_source(self.source, self.client_http)
        seen = Item.objects.get().first_seen_at
        second = ingest_source(self.source, self.client_http)
        self.assertEqual((first.created, second.created, second.unchanged), (1, 0, 1))
        self.assertEqual(Item.objects.get().versions.count(), 1)
        self.client_http.get.return_value = response(RSS.replace(b"Komunikat", b"Aktualizacja"))
        third = ingest_source(self.source, self.client_http)
        self.assertEqual(third.updated, 1)
        self.assertEqual(Item.objects.get().versions.count(), 2)
        self.assertEqual(Item.objects.get().first_seen_at, seen)

    def test_failure_backoff_and_recovery(self):
        self.client_http.get.side_effect = FetchError("busy", 429, 900)
        failed = ingest_source(self.source, self.client_http)
        self.source.refresh_from_db()
        self.assertEqual(failed.status, "failed")
        self.assertGreater(self.source.next_fetch_at, timezone.now() + timedelta(seconds=890))
        self.client_http.get.side_effect = None
        ingest_source(self.source, self.client_http)
        self.source.refresh_from_db()
        self.assertEqual(self.source.failures, 0)

    def test_304_keeps_items_and_cache_validators(self):
        ingest_source(self.source, self.client_http)
        self.client_http.get.return_value = response(status=304, body=b"")
        run = ingest_source(self.source, self.client_http)
        self.assertEqual(run.status, "not_modified")
        self.assertEqual(Item.objects.count(), 1)
        self.source.refresh_from_db()
        self.assertEqual(self.source.etag, '"v1"')

    def test_failure_isolation(self):
        other = Source.objects.create(
            slug="other",
            name="Other",
            adapter="feed",
            enabled=True,
            url="https://www.gov.pl/web/gios/other",
        )
        with patch("ingestion.service.Client") as client:
            client.return_value.get.side_effect = lambda url, headers: (
                response(b"bad") if url == other.url else response()
            )
            result = run_batch(force=True)
        self.assertEqual({row["status"] for row in result}, {"success", "failed"})
        self.assertEqual(Item.objects.count(), 1)

    def test_disabled_and_not_due_sources_are_skipped(self):
        self.source.next_fetch_at = timezone.now() + timedelta(days=1)
        self.source.save()
        with patch("ingestion.service.Client") as client:
            self.assertEqual(run_batch(), [])
            client.return_value.get.assert_not_called()
        self.source.enabled = False
        self.source.save()
        self.assertEqual(run_batch(force=True), [])

    def test_dry_run_does_not_write(self):
        with patch("ingestion.service.Client", return_value=self.client_http):
            self.assertEqual(run_batch(dry_run=True)[0]["count"], 1)
        self.assertEqual(Item.objects.count(), 0)
        self.assertEqual(IngestionRun.objects.count(), 0)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.last_success_at)

    def test_soft_redirect_rejected(self):
        self.client_http.get.return_value = response(url="https://www.gov.pl/")
        with self.assertRaises(FetchError):
            fetch_entries(self.source, self.client_http)

    def test_seed_preserves_editorial_changes(self):
        call_command("seed_sources", stdout=io.StringIO())
        Source.objects.filter(slug="gios").update(name="Editorial change", enabled=True)
        call_command("seed_sources", stdout=io.StringIO())
        self.assertEqual(Source.objects.get(slug="gios").name, "Editorial change")
        self.assertEqual(Source.objects.count(), 21)

    def test_item_html_is_escaped(self):
        Item.objects.create(
            source=self.source,
            external_id="id",
            title="<script>x</script>",
            url="https://www.gov.pl/a",
            content_hash="hash",
        )
        page = self.client.get("/")
        self.assertContains(page, "&lt;script&gt;")
        self.assertNotContains(page, "<script>x</script>")


class TransportTests(SimpleTestCase):
    @patch("ingestion.transport.socket.getaddrinfo")
    def test_ssrf_destinations_blocked(self, dns):
        dns.return_value = [(None, None, None, None, ("127.0.0.1", 443))]
        for url in ("http://www.gov.pl/a", "https://localhost/a", "https://www.gov.pl/a"):
            with self.assertRaises(FetchError):
                validate_url(url)

    def test_robots_disallow_prevents_content_request(self):
        client = Client()
        with patch.object(
            client, "_request", return_value=response(b"User-agent: *\nDisallow: /")
        ) as req:
            with self.assertRaises(FetchError):
                client.get("https://www.gov.pl/a")
            self.assertEqual(req.call_count, 1)

    def test_retry_after_is_deferred_and_retry_bounded(self):
        client = Client()
        with patch.object(
            client, "_raw", return_value=response(status=429, headers={"retry-after": "600"})
        ) as raw:
            with self.assertRaises(FetchError) as caught:
                client._request("https://www.gov.pl/a")
            self.assertEqual(caught.exception.retry_after, 600)
            self.assertEqual(raw.call_count, 1)
        with (
            patch.object(client, "_raw", return_value=response(status=503)) as raw,
            patch("ingestion.transport.time.sleep"),
        ):
            with self.assertRaises(FetchError):
                client._request("https://www.gov.pl/a")
            self.assertEqual(raw.call_count, 3)
        self.assertEqual(retry_delay("garbage"), 0)

    def test_robots_404_allows_content(self):
        client = Client()
        with patch.object(client, "_request", side_effect=[response(status=404), response()]):
            self.assertEqual(client.get("https://www.gov.pl/a").status, 200)

    def test_robot_rules_fixture(self):
        rules = RobotFileParser()
        rules.parse(["User-agent: *", "Disallow: /private"])
        self.assertFalse(rules.can_fetch("GeoKlimatMonitor", "https://www.gov.pl/private"))
