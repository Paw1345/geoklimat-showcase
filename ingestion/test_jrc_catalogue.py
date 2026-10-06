import io
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.jrc_catalogue import CONFIG, FIELDS, SLUG, THEME, URL, XSD, fetch_catalogue
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import FetchError, Response


def row(
    key="a", title="Environmental data", description="Public description", modified="2025-09-12"
):
    value = {
        "dataset": {"type": "uri", "value": "http://data.europa.eu/89h/" + key},
        "title": {"type": "literal", "xml:lang": "en", "value": title},
    }
    if description is not None:
        value["description"] = {"type": "literal", "xml:lang": "en", "value": description}
    if modified is not None:
        value["modified"] = {"type": "literal", "datatype": XSD + "date", "value": modified}
    return value


def response(url, rows, *, fields=None):
    return Response(
        200,
        json.dumps({"head": {"vars": fields or FIELDS}, "results": {"bindings": rows}}).encode(),
        {},
        url,
    )


class JrcCatalogueTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug=SLUG,
            adapter="json",
            url=URL,
            config=CONFIG.copy(),
            enabled=True,
            activation_approved=True,
        )
        self.client_http = Mock()
        self.client_http.get.side_effect = lambda url, **kwargs: response(url, [row()])

    def test_scope_and_contract_no_resources_or_invented_dates(self):
        entries, rejected, result, digest = fetch_entries(self.source, self.client_http)
        self.assertEqual((len(entries), rejected, result.status), (1, 0, 200))
        self.assertEqual(len(digest), 64)
        entry = entries[0]
        self.assertEqual(entry.url, "https://data.jrc.ec.europa.eu/dataset/a")
        self.assertEqual(entry.metadata["theme_uri"], THEME)
        self.assertEqual(entry.metadata["dataset_modified_raw"], "2025-09-12")
        self.assertEqual(entry.metadata["record_kind"], "dataset_collection")
        self.assertFalse(entry.metadata["resources_downloaded"])
        self.assertEqual((entry.published, entry.updated), ("", ""))
        self.client_http.get.assert_called_once()
        args, kwargs = self.client_http.get.call_args
        self.assertEqual(urlsplit(args[0]).path, "/sparql")
        query = parse_qs(urlsplit(args[0]).query)["query"][0]
        self.assertIn(THEME, query)
        self.assertIn('lang(?title)="en"', query)
        self.assertIn("LIMIT 100 OFFSET 0", query)
        self.assertEqual(kwargs["headers"]["Accept"], "application/sparql-results+json")

    def test_optional_descriptions_dates_and_raw_datetime_no_timezone_inference(self):
        rows = [row("a", description=None, modified=None), row("b")]
        rows[1]["modified"] = {
            "type": "literal",
            "datatype": XSD + "dateTime",
            "value": "2026-09-29T13:29:56",
        }
        self.client_http.get.side_effect = lambda url, **kwargs: response(url, rows)
        entries, _, _, _ = fetch_catalogue(self.source, self.client_http)
        self.assertEqual(entries[0].summary, "")
        self.assertEqual(entries[0].metadata["dataset_modified_raw"], "")
        self.assertEqual(entries[1].metadata["dataset_modified_raw"], "2026-09-29T13:29:56")
        self.assertEqual(entries[1].updated, "")

    def test_pagination_and_no_partial_commit_on_failure(self):
        batches = [[row("a"), row("b")], [row("c")]]
        self.client_http.get.side_effect = lambda url, **kwargs: response(url, batches.pop(0))
        with patch("ingestion.jrc_catalogue.PAGE_SIZE", 2):
            entries, _, _, _ = fetch_entries(self.source, self.client_http)
        self.assertEqual(len(entries), 3)
        self.assertIn(
            "OFFSET 2", parse_qs(urlsplit(self.client_http.get.call_args.args[0]).query)["query"][0]
        )
        batches = [[row("a"), row("b")], [row("b")]]
        self.client_http.get.side_effect = lambda url, **kwargs: response(url, batches.pop(0))
        with (
            patch("ingestion.jrc_catalogue.PAGE_SIZE", 2),
            patch("ingestion.service.Client", return_value=self.client_http),
        ):
            result = run_batch(SLUG, force=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertEqual(Item.objects.filter(source=self.source).count(), 0)
        self.assertEqual(self.source.runs.get().created, 0)

    def test_budget_status_redirect_and_config_fail_closed(self):
        with (
            patch("ingestion.jrc_catalogue.PAGE_SIZE", 1),
            patch("ingestion.jrc_catalogue.MAX_PAGES", 1),
            self.assertRaises(FetchError),
        ):
            fetch_catalogue(self.source, self.client_http)
        for status, url in [(304, URL), (200, "https://example.org/"), (206, URL)]:
            self.client_http.get.side_effect = None
            self.client_http.get.return_value = Response(status, b"{}", {}, url)
            with self.subTest(status=status, url=url), self.assertRaises(FetchError):
                fetch_catalogue(self.source, self.client_http)
        self.source.config = {**CONFIG, "theme": "ALL"}
        self.client_http.reset_mock()
        with self.assertRaises(FetchError):
            fetch_catalogue(self.source, self.client_http)
        self.client_http.get.assert_not_called()

    def test_identity_schema_language_order_and_dates_fail_closed(self):
        invalid = []
        for field, binding in [
            ("dataset", {"type": "uri", "value": "https://example.org/a"}),
            ("dataset", {"type": "uri", "value": "http://data.europa.eu/89h/../a"}),
            ("title", {"type": "literal", "xml:lang": "pl", "value": "Dane"}),
            ("title", {"type": "literal", "xml:lang": "en", "value": " "}),
            ("description", {"type": "literal", "xml:lang": "en", "value": 3}),
            ("modified", {"type": "literal", "datatype": XSD + "date", "value": "2025-02-30"}),
            ("modified", {"type": "literal", "datatype": XSD + "string", "value": "2025-09-12"}),
        ]:
            changed = row()
            changed[field] = binding
            invalid.append([changed])
        invalid += [[row("a"), row("a")], [row("b"), row("a")], [None], [dict(row(), extra="bad")]]
        for rows in invalid:
            with self.subTest(rows=rows):
                self.client_http.get.side_effect = lambda url, rows=rows, **kwargs: response(
                    url, rows
                )
                with self.assertRaises(FetchError):
                    fetch_catalogue(self.source, self.client_http)
        for body in [
            b"not json",
            b"null",
            b"[]",
            b'{"head":null}',
            json.dumps({"head": {"vars": FIELDS}, "results": {"bindings": None}}).encode(),
        ]:
            self.client_http.get.side_effect = lambda url, body=body, **kwargs: Response(
                200, body, {}, url
            )
            with self.subTest(body=body), self.assertRaises(FetchError):
                fetch_catalogue(self.source, self.client_http)

    def test_repeat_import_and_description_change_with_history(self):
        with patch("ingestion.service.Client", return_value=self.client_http):
            first = run_batch(SLUG, force=True)[0]
            second = run_batch(SLUG, force=True)[0]
            self.client_http.get.side_effect = lambda url, **kwargs: response(
                url, [row(description="Changed description")]
            )
            third = run_batch(SLUG, force=True)[0]
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(third["updated"], 1)
        item = Item.objects.get(source=self.source)
        self.assertEqual(item.summary, "Changed description")
        self.assertEqual(item.versions.count(), 2)
        self.assertIsNone(item.published_at)
        self.assertIsNone(item.source_updated_at)

    def test_configuration_preserves_gap_workbook_audit_and_admin_edits(self):
        self.source.delete()
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=181,
            release=release,
            organization="JRC",
            name="Data Catalogue",
            priority="P1",
            master={"original": True},
            technical={"Reuse / licence": "Verify"},
            integration_state="needs_connector",
        )
        with patch("ingestion.transport.Client.get") as get:
            call_command("configure_jrc", stdout=io.StringIO())
            self.assertFalse(Source.objects.filter(slug=SLUG).exists())
            call_command("configure_jrc", apply=True, stdout=io.StringIO())
            get.assert_not_called()
        source = Source.objects.get(slug=SLUG)
        self.assertFalse(source.enabled or source.activation_approved)
        self.assertEqual(source.interval_minutes, 360)
        source.last_audit = {"status": "verified"}
        source.enabled = True
        source.activation_approved = True
        source.save()
        call_command("configure_jrc", apply=True, stdout=io.StringIO())
        source.refresh_from_db()
        registry.refresh_from_db()
        self.assertEqual(source.last_audit, {"status": "verified"})
        self.assertTrue(source.enabled and source.activation_approved)
        self.assertEqual(registry.integration_state, "needs_connector")
        self.assertEqual(registry.master, {"original": True})
        source.url = "https://example.org/edited"
        source.save()
        with self.assertRaises(CommandError):
            call_command("configure_jrc", apply=True, stdout=io.StringIO())
