import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.core.management import call_command
from django.test import TestCase

from ingestion.adapters import parse
from ingestion.classification import classify
from ingestion.models import Item, RegistryEntry, RegistryRelease, Source
from ingestion.service import fetch_entries, run_batch
from ingestion.transport import FetchError, Response
from ingestion.workbook_registry import read_registry, xml


def _excel_column(number):
    letters = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _write_synthetic_registry(path):
    sheets = {
        "Master Registry": [
            [
                "ID",
                "Organization",
                "Source / Endpoint",
                "Country/Area",
                "Level",
                "Priority",
                "Poll interval (min)",
                "Endpoint URL",
            ],
            [9001, "Demo Institute", "Demo public feed", "Poland", "national", "P2", 60, "https://example.org/demo"],
        ],
        "Summary": [["Key", "Value"], ["fixture", "synthetic"]],
        "Taxonomy": [["Code", "Name", "Includes"], ["demo_topic", "Demo topic", "sample"]],
        "Technical Verification": [
            ["ID", "Technical status", "Reuse / licence", "Exact machine endpoint / feed"],
            [9001, "READY_STRUCTURED", "Synthetic test data", "https://example.org/demo/api"],
        ],
        "Verification Evidence": [["ID", "Reference"], [9001, "Synthetic test evidence"]],
        "Implementation Queue": [["ID", "Work item"], [9001, "Review demo scope"]],
    }
    names = list(sheets)
    content_types = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    workbook_sheets = []
    relationships = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    worksheet_files = []
    for index, name in enumerate(names, start=1):
        workbook_sheets.append(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        )
        relationships.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        rows = []
        for row_number, values in enumerate(sheets[name], start=1):
            cells = []
            for column_number, value in enumerate(values, start=1):
                address = f"{_excel_column(column_number)}{row_number}"
                if isinstance(value, str):
                    cells.append(
                        f'<c r="{address}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                    )
                else:
                    cells.append(f'<c r="{address}"><v>{value}</v></c>')
            rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        worksheet_files.append((
            f'xl/worksheets/sheet{index}.xml',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(rows)}</sheetData></worksheet>',
        ))
    content_types.append("</Types>")
    relationships.append("</Relationships>")
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", "".join(relationships))
        for filename, content in worksheet_files:
            archive.writestr(filename, content)


class RegistryTests(TestCase):
    def test_activate_only_fresh_audited_public_channels(self):
        from datetime import timedelta

        from django.utils import timezone

        good = Source.objects.create(
            slug="registry-001",
            name="Public",
            adapter="web",
            url="https://www.gov.pl/test",
            audited_at=timezone.now(),
            activation_approved=False,
            last_audit={
                "status": "verified",
                "count": 1,
                "rejected": 0,
                "url": "https://www.gov.pl/test",
            },
        )
        stale = Source.objects.create(
            slug="registry-002",
            name="Stale",
            adapter="web",
            url=good.url,
            audited_at=timezone.now() - timedelta(days=2),
            activation_approved=False,
            last_audit=good.last_audit,
        )
        changed = Source.objects.create(
            slug="registry-003",
            name="Changed",
            adapter="web",
            url="https://www.gov.pl/other",
            audited_at=timezone.now(),
            activation_approved=False,
            last_audit=good.last_audit,
        )
        call_command("activate_registry", stdout=io.StringIO())
        self.assertFalse(Source.objects.filter(enabled=True).exists())
        call_command("activate_registry", apply=True, stdout=io.StringIO())
        good.refresh_from_db()
        self.assertTrue(good.enabled and good.activation_approved)
        self.assertFalse(
            Source.objects.filter(pk__in=[stale.pk, changed.pk], enabled=True).exists()
        )

    def test_workbook_complete_and_strict_xml(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-registry.xlsx"
            _write_synthetic_registry(path)
            data = read_registry(path)
        self.assertEqual(len(data["tables"]["Master Registry"]), 1)
        self.assertEqual(len(data["tables"]["Technical Verification"]), 1)
        self.assertEqual(len(data["tables"]["Verification Evidence"]), 1)
        self.assertEqual(len(data["tables"]["Implementation Queue"]), 1)
        self.assertEqual(len(data["tables"]["Taxonomy"]), 1)
        with self.assertRaises(ValueError):
            xml(b'<!DOCTYPE a [<!ENTITY b "bad">]><a>&b;</a>')

    def test_import_dry_run_idempotence_and_preservation(self):
        call_command("seed_sources", stdout=io.StringIO())
        original = Source.objects.get(slug="gios")
        original.enabled = True
        original.save()
        item = Item.objects.create(
            source=original, external_id="kept", title="Kept", url="https://www.gov.pl/test"
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic-registry.xlsx"
            _write_synthetic_registry(path)
            call_command("import_registry", str(path), stdout=io.StringIO())
            self.assertFalse(RegistryEntry.objects.exists())
            call_command("import_registry", str(path), apply=True, stdout=io.StringIO())
            sources = Source.objects.count()
            call_command("import_registry", str(path), apply=True, stdout=io.StringIO())
        self.assertEqual(Source.objects.count(), sources)
        self.assertEqual(RegistryEntry.objects.count(), 1)
        self.assertEqual(RegistryRelease.objects.count(), 1)
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        original.refresh_from_db()
        self.assertTrue(original.enabled)
        self.assertFalse(Source.objects.filter(slug__startswith="registry-", enabled=True).exists())
        self.assertEqual(
            RegistryEntry.objects.get(registry_id=9001).integration_state, "needs_connector"
        )
        self.assertContains(self.client.get("/registry/9001/"), "needs_connector")
        self.assertContains(
            self.client.get("/registry/workbook/?sheet=Taxonomy"), "demo_topic"
        )
        self.assertEqual(self.client.get("/registry/9999/").status_code, 404)

    def test_activation_gate(self):
        Source.objects.create(
            slug="blocked",
            name="Blocked",
            url="https://www.gov.pl/test",
            adapter="web",
            enabled=True,
            activation_approved=False,
        )
        with patch("ingestion.service.Client") as client:
            self.assertEqual(run_batch(force=True), [])
            client.return_value.get.assert_not_called()

    def test_new_taxonomy(self):
        source = Source(adapter="html")
        for title, expected in [
            ("CBAM i ETS", "emissions_ets_cbam"),
            ("Osuwiska i geologia", "soil_geology"),
            ("Zmiany REACH", "chemicals_pollution"),
            ("OZE offshore", "energy"),
            ("Zmiana API", "technical_changes"),
        ]:
            self.assertIn(expected, classify(Item(title=title, source=source))["topics"])


class ExtendedAdapterTests(TestCase):
    def test_gov_root_and_subsection_redirect_boundaries(self):
        root = "https://www.gov.pl/web/rdos-bialystok"
        source = Source(adapter="web", url=root)
        body = (
            b'<div class="art-prev"><li><div class="title">'
            b'<a href="/web/rdos-bialystok/news">News</a></div></li></div>'
        )
        client = Mock()
        for requested, final, allowed in (
            (root, root, True),
            (root + "/", root, True),
            (root + "/aktualnosci", root + "/aktualnosci", True),
            (root + "/missing", root, False),
            (root, root + "-other/news", False),
            (root, "https://www.gov.pl/web/other/news", False),
            (root, "https://other.example/web/rdos-bialystok", False),
        ):
            with self.subTest(requested=requested, final=final):
                source.url = requested
                client.get.return_value = Response(200, body, {}, final)
                if allowed:
                    entries, rejected, _, _ = fetch_entries(source, client)
                    self.assertEqual((len(entries), rejected), (1, 0))
                else:
                    with self.assertRaises(FetchError):
                        fetch_entries(source, client)

    def test_stac_pagination_and_cross_host_rejection(self):
        source = Source(adapter="stac", url="https://stac.dataspace.copernicus.eu/v1/collections")
        client = Mock()
        client.get.side_effect = [
            self.response(
                {
                    "collections": [{"id": "one"}],
                    "links": [{"rel": "next", "href": source.url + "?offset=1", "method": "GET"}],
                }
            ),
            self.response({"collections": [{"id": "two"}]}),
        ]
        entries, bad, _, _ = fetch_entries(source, client)
        self.assertEqual(len(entries), 2)
        self.assertEqual(bad, 0)
        client.get.side_effect = None
        client.get.return_value = self.response(
            {
                "collections": [{"id": "one"}],
                "links": [{"rel": "next", "href": "https://unapproved.example/collections"}],
            }
        )
        with self.assertRaises(FetchError):
            fetch_entries(source, client)

    def response(self, payload, url="https://www.gov.pl/test"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return Response(200, body, {}, url)

    def test_gios_and_sejm_records(self):
        source = Source(
            adapter="json",
            url="https://api.gios.gov.pl/pjp-api/v1/rest/station/findAll",
            config={"mode": "gios"},
        )
        items, _ = parse(
            source,
            self.response(
                {
                    "Lista stacji pomiarowych": [
                        {"Identyfikator stacji": 1, "Nazwa stacji": "Stacja A"}
                    ]
                }
            ),
        )
        self.assertEqual(items[0].metadata["record_kind"], "metadata")
        source.config = {"mode": "sejm"}
        items, _ = parse(
            source,
            self.response([{"number": "42", "title": "Projekt", "documentDate": "2026-09-18"}]),
        )
        self.assertEqual(items[0].published, "2026-09-18")
        with self.assertRaises(FetchError):
            parse(source, self.response({"error": "bad"}))

    def test_pagination_and_budget(self):
        source = Source(
            adapter="json",
            url="https://api.sejm.gov.pl/sejm/term10/processes",
            config={"mode": "sejm", "page_size": 1, "max_pages": 3},
        )
        client = Mock()
        client.get.side_effect = [
            self.response([{"number": "1", "title": "A"}]),
            self.response([{"number": "2", "title": "B"}]),
            self.response([]),
        ]
        entries, _, _, _ = fetch_entries(source, client)
        self.assertEqual(len(entries), 2)
        self.assertIn("offset=1", client.get.call_args_list[1].args[0])
        client.get.side_effect = None
        client.get.return_value = self.response([{"number": "1", "title": "A"}])
        with self.assertRaises(FetchError):
            fetch_entries(source, client)

    def test_stac_no_asset_download(self):
        source = Source(adapter="stac", url="https://stac.dataspace.copernicus.eu/v1/collections")
        items, _ = parse(
            source,
            self.response(
                {"collections": [{"id": "sentinel", "title": "Sentinel", "license": "open"}]}
            ),
        )
        self.assertEqual(items[0].metadata["record_kind"], "dataset_collection")
        with self.assertRaises(FetchError):
            parse(
                source,
                self.response(
                    {"collections": [], "links": [{"rel": "next", "href": "https://bad.invalid"}]}
                ),
            )

    def test_ogc_and_hostile_xml(self):
        source = Source(
            adapter="ogc",
            name="WMS",
            url="https://www.geoportal.gov.pl/test",
            config={"service": "WMS"},
        )
        items, _ = parse(
            source,
            self.response(
                b'<WMS_Capabilities version="1.3.0"><Service><Title>WMS</Title>'
                b"</Service></WMS_Capabilities>"
            ),
        )
        self.assertEqual(items[0].metadata["version"], "1.3.0")
        for invalid in (b"<html>Not a service</html>", b"<!DOCTYPE a><a/>", b"<ExceptionReport/>"):
            with self.assertRaises(FetchError):
                parse(source, self.response(invalid))

    def test_web_listing_snapshot_and_js(self):
        source = Source(adapter="web", url="https://www.gov.pl/test", name="Page")
        items, _ = parse(
            source,
            self.response(
                b'<div class="art-prev"><li><div class="title">'
                b'<a href="/news">News</a></div></li></div>'
            ),
        )
        self.assertEqual(items[0].metadata["record_kind"], "news")
        items, _ = parse(
            source, self.response(b"<main>" + b"Publication content " * 20 + b"</main>")
        )
        self.assertEqual(items[0].metadata["record_kind"], "page_snapshot")
        for body in (
            b"<html><div id='app'></div></html>",
            b"<main>enable javascript " + b"wait " * 50 + b"</main>",
        ):
            with self.assertRaises(FetchError):
                parse(source, self.response(body))
