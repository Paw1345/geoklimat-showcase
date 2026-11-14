import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion import gios_major_accidents as gma
from ingestion.data_service import run_data, store_snapshot
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

FIXTURE = Path(__file__).parent / "fixtures" / "gios_accidents_2024_989495.xlsx"
SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def xlsx(rows, sheets=1, prefix="", formula=""):
    def cell(column, number, value):
        ref = chr(65 + column) + str(number)
        return f'<c r="{ref}" t="inlineStr">{formula}<is><t>{escape(value)}</t></is></c>'

    body = "".join(
        f'<row r="{n}">' + "".join(cell(c, n, v) for c, v in enumerate(row)) + "</row>"
        for n, row in enumerate(rows, 1)
    )
    names = "".join(
        f'<sheet name="S{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, sheets + 1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheets + 1)
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{SHEET_NS}" xmlns:r="{REL_NS}"><sheets>{names}</sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + rels
            + "</Relationships>",
        )
        for i in range(1, sheets + 1):
            archive.writestr(
                f"xl/worksheets/sheet{i}.xml",
                f'{prefix}<worksheet xmlns="{SHEET_NS}"><sheetData>{body}</sheetData></worksheet>',
            )
    return out.getvalue()


def register_rows(category="ZDR"):
    return [
        [f"LICZBA {category} ", *gma.REGISTER_TAIL],
        ["1.", "DOLNOŚLĄSKIE", "Zakład A ", "50-001 Wrocław\nul. A 1", "https://a.example/info"],
        ["2.", "KUJAWSKO POMORSKIE", "Zakład B", "87-100 Toruń", ""],
    ]


def accident_rows(year=2023, header=None, victims=("-", "-")):
    serial = {2022: "44845", 2023: "44949", 2024: "45302"}[year]
    header = header or (gma.ACCIDENTS_2022 if year == 2022 else gma.ACCIDENTS)
    row = ["1.", "Mazowieckie", serial, "Płock\ngm. Płock", "zakład inny", "pożar", "Opis"]
    row += ["§ 4", "Skutki", "Toluen", *victims]
    if year == 2022:
        row.insert(4, "ul. Długa 1")
    return [header, row]


class GiosMajorAccidentTests(TestCase):
    def test_real_2024_fixture_dates_victims_and_identity(self):
        records = gma.parse_table("accidents_2024", FIXTURE.read_bytes())
        self.assertEqual(len(records), 8)
        first = records[0]
        self.assertEqual(first["id"], "989495:1")
        self.assertEqual((first["date"], first["year"]), ("2024-01-11", 2024))
        self.assertEqual((first["victims_total"], first["victims_fatal"]), (1, 1))
        self.assertEqual(first["voivodeship"], "mazowieckie")
        self.assertEqual(first["accident_type"], "pożar")
        self.assertTrue(first["pollutants"].startswith("Toluen"))
        self.assertEqual([r["source_row_number"] for r in records], list(range(1, 9)))

    def test_register_parsing_alias_and_fail_closed_changes(self):
        records = gma.parse_table("zdr_2024", xlsx(register_rows()))
        self.assertEqual(
            [r["voivodeship"] for r in records], ["dolnośląskie", "kujawsko-pomorskie"]
        )
        self.assertEqual(records[0]["establishment"], "Zakład A")
        self.assertEqual(records[0]["address"], "50-001 Wrocław ul. A 1")
        self.assertEqual((records[0]["category"], records[0]["as_of"]), ("ZDR", "2024-12-31"))
        rows = register_rows()
        changes = [
            [rows[0][:4] + ["Inna kolumna"], *rows[1:]],
            [rows[0], ["3.", *rows[1][1:]], rows[2]],
            [rows[0], rows[1], [""] * 5, rows[2]],
            [rows[0], rows[1] + ["extra"], rows[2]],
            [rows[0], [rows[1][0], "Bawaria", *rows[1][2:]], rows[2]],
            [rows[0], [*rows[1][:2], " ", *rows[1][3:]], rows[2]],
            [rows[0]],
        ]
        for index, changed in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(FetchError):
                gma.parse_table("zdr_2024", xlsx(changed))
        with self.assertRaises(FetchError):
            gma.parse_table("zzr_2024", xlsx(register_rows()))

    def test_accident_dash_is_unknown_not_zero(self):
        record = gma.parse_table("accidents_2023", xlsx(accident_rows()))[0]
        self.assertIsNone(record["victims_total"])
        self.assertEqual(record["victims_source_text"], ["-", "-"])
        street = gma.parse_table("accidents_2022", xlsx(accident_rows(2022)))[0]
        self.assertEqual((street["street"], street["date"]), ("ul. Długa 1", "2022-10-11"))
        for index, rows in enumerate(
            [
                accident_rows(victims=("-", "0")),
                accident_rows(victims=("1", "2")),
                accident_rows(victims=("jeden", "0")),
                accident_rows(2024),
                accident_rows(header=gma.ACCIDENTS_2022),
            ]
        ):
            with self.subTest(index=index), self.assertRaises(FetchError):
                gma.parse_table("accidents_2023", xlsx(rows))

    def test_xlsx_reader_rejects_formulas_multiple_sheets_and_dtd(self):
        rows = register_rows()
        with self.assertRaises(FetchError):
            gma.parse_table("zdr_2024", xlsx(rows, sheets=2))
        formula = xlsx(rows, formula="<f>1+1</f>")
        dtd = xlsx(rows, prefix="<!DOCTYPE x>")
        for body in (formula, dtd, b"not a zip"):
            with self.subTest(body=body[:10]), self.assertRaises(FetchError):
                gma.parse_table("zdr_2024", body)

    def fake_client(self, files, mutate=None):
        responses = {}
        for key in (137, 2540):
            responses[f"{gma.API}datasets/{key}"] = {
                "type": "dataset",
                "id": str(key),
                "attributes": {"license_name": "CC BY 4.0", "license_condition_default_cc40": True},
                "relationships": {"institution": {"data": {"type": "institution", "id": "100"}}},
            }
        for spec in gma.RESOURCES.values():
            responses[f"{gma.API}resources/{spec['resource']}"] = {
                "type": "resource",
                "id": str(spec["resource"]),
                "attributes": {
                    "link": spec["link"],
                    "file_size": spec["size"],
                    "format": "xlsx",
                    "title": spec["title"],
                    "contains_protected_data": False,
                },
                "relationships": {
                    "dataset": {"data": {"type": "dataset", "id": str(spec["dataset"])}}
                },
            }
        if mutate:
            mutate(responses)
        bodies = {gma.RESOURCES[key]["link"]: body for key, body in files.items()}

        class Client:
            def get(self, url):
                if url in bodies:
                    return Response(200, bodies[url], {}, url)
                return Response(200, json.dumps({"data": responses[url]}).encode(), {}, url)

        return Client()

    def files(self):
        files = {
            "zdr_2024": xlsx(register_rows("ZDR")),
            "zzr_2024": xlsx(register_rows("ZZR")),
            "accidents_2024": FIXTURE.read_bytes(),
            "accidents_2023": xlsx(accident_rows(2023)),
            "accidents_2022": xlsx(accident_rows(2022)),
        }
        sizes = {key: {**gma.RESOURCES[key], "size": len(body)} for key, body in files.items()}
        return files, sizes

    def channel(self):
        return DataChannel(url=gma.URL, region="PL", kind="table", config=gma.CONFIG.copy())

    def test_collect_pins_catalogue_licence_and_files(self):
        files, sizes = self.files()
        with patch.dict(gma.RESOURCES, sizes):
            payload, media, summary = gma.collect(self.channel(), self.fake_client(files))
            data = json.loads(payload)
            self.assertEqual(media, "application/json")
            self.assertEqual(summary["registers"], {"ZDR": 2, "ZZR": 2})
            self.assertEqual(summary["accident_years"], [2022, 2023, 2024])
            self.assertEqual(summary["records"], 4 + 10)
            self.assertFalse(summary["new_api_used"])
            self.assertEqual(data["license"], "CC BY 4.0")
            self.assertEqual(len(data["sources"]), 5)
            self.assertEqual(data["accidents"][0]["date"], "2022-10-11")

            def licence(r):
                r[f"{gma.API}datasets/137"]["attributes"]["license_name"] = "CC BY-NC 4.0"

            def protected(r):
                r[f"{gma.API}resources/989557"]["attributes"]["contains_protected_data"] = True

            def size(r):
                r[f"{gma.API}resources/989495"]["attributes"]["file_size"] = 1

            def owner(r):
                r[f"{gma.API}datasets/2540"]["relationships"]["institution"]["data"]["id"] = "9"

            for mutate in (licence, protected, size, owner):
                with self.subTest(mutate=mutate.__name__), self.assertRaises(FetchError):
                    gma.collect(self.channel(), self.fake_client(files, mutate))
            changed = self.channel()
            changed.config = {**gma.CONFIG, "extra": True}
            with self.assertRaises(FetchError):
                gma.collect(changed, self.fake_client(files))

    def test_configure_dry_run_apply_idempotent_and_dispatch(self):
        release = RegistryRelease.objects.create(filename="t", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=50, release=release, priority="P2", master={}, technical={}
        )
        call_command("configure_gios_major_accidents", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_gios_major_accidents", apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get(slug=gma.SLUG)
        self.assertEqual(
            (channel.kind, channel.enabled, channel.access_approved), ("table", False, False)
        )
        self.assertEqual(channel.interval_minutes, 360)
        call_command("configure_gios_major_accidents", apply=True, stdout=io.StringIO())
        entry.refresh_from_db()
        self.assertEqual(entry.integration_state, "needs_endpoint")
        files, sizes = self.files()
        with (
            patch.dict(gma.RESOURCES, sizes),
            patch("ingestion.data_service.Client", lambda: self.fake_client(files)),
        ):
            result = run_data(gma.SLUG, dry_run=True)[0]
        self.assertEqual((result["status"], result["records"]), ("dry_run", 14))
        channel.config = {**gma.CONFIG, "admin": 1}
        channel.save()
        with self.assertRaises(CommandError):
            call_command("configure_gios_major_accidents", apply=True, stdout=io.StringIO())

    def test_data_page_states_scope_and_unknown_victims(self):
        release = RegistryRelease.objects.create(filename="t", sha256="b" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=50, release=release, priority="P2", master={}, technical={}
        )
        channel = DataChannel.objects.create(
            registry=entry,
            slug=gma.SLUG,
            name="GIOŚ awarie",
            kind="table",
            url=gma.URL,
            config=gma.CONFIG.copy(),
            region="PL",
            attribution=gma.ATTRIBUTION,
        )
        summary = {
            "kind": "gios_major_accidents",
            "collector": "gios_major_accidents",
            "scope": "PL",
            "records": 3,
            "registers": {"ZDR": 1, "ZZR": 1},
            "register_as_of": "2024-12-31",
            "accidents": 1,
            "accident_years": [2024],
        }
        store_snapshot(channel, b'{"registers": []}', "application/json", summary)
        page = self.client.get(f"/data/{channel.pk}/").content.decode()
        self.assertIn("ZDR 1, ZZR 1", page)
        self.assertIn("nie nowe API GIOŚ", page)
        snapshot = channel.snapshots.get()
        export = self.client.get(f"/data/snapshots/{snapshot.pk}/download/")
        self.assertEqual((export.status_code, export.content), (200, b'{"registers": []}'))
