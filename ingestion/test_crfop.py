import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion import crfop
from ingestion import gios_tables as gt
from ingestion.data_service import run_data
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease
from ingestion.test_gios_major_accidents import xlsx
from ingestion.transport import FetchError, Response

FIXTURE = Path(__file__).parent / "fixtures" / "crfop_parki_narodowe_1187255.xlsx"
HEADER = [
    "fop_id",
    "inspire_id",
    "typ_formy_nazwa",
    "archiwalny",
    "nazwa",
    "data_utworzenia",
    "powierzchnia",
    *crfop.ACT_COLUMNS,
    *crfop.CONVENTION_COLUMNS,
]


def form(fop_id="10", name="Rezerwat A", area="12.5000", act=("utworzenie", "Zarządzenie")):
    return [
        fop_id,
        f"PL.ZIPOP.1393.RP.{fop_id}",
        "rezerwat przyrody",
        "t",
        name,
        "1959-01-01",
        area,
        *act,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ][: len(HEADER)]


def act_row(name="Zmiana", day="2001-02-03"):
    row = [""] * len(HEADER)
    row[HEADER.index("rodzaj_aktu_nazwa")] = "zmiana"
    row[HEADER.index("akt_prawny_nazwa")] = name
    row[HEADER.index("akt_prawny_data_pub")] = day
    return row


def pinned(body, **changes):
    spec = crfop.REGISTERS["rezerwaty-przyrody"]
    return {**spec, "size": len(body), "sha256": hashlib.sha256(body).hexdigest(), **changes}


class CrfopTests(TestCase):
    def test_real_national_parks_fixture(self):
        spec = crfop.REGISTERS["parki-narodowe"]
        table = crfop.parse_register(spec, FIXTURE.read_bytes())
        self.assertEqual(len(table["forms"]), 23)
        self.assertEqual(sum(len(f["legal_acts"]) for f in table["forms"]), 199)
        swietokrzyski = next(f for f in table["forms"] if f["id"] == 2485)
        values = dict(zip(table["form_columns"], swietokrzyski["values"], strict=True))
        self.assertEqual(values["nazwa"], "Świętokrzyski Park Narodowy")
        self.assertEqual(values["inspire_id"], "PL.ZIPOP.1393.PN.6")
        self.assertEqual(values["powierzchnia"], "7696.0424")
        self.assertEqual([f["id"] for f in table["forms"]], sorted(f["id"] for f in table["forms"]))

    def test_continuation_rows_attach_acts_and_fail_closed(self):
        body = xlsx([HEADER, form(), act_row(), form("11", name=""), act_row("Druga")])
        table = crfop.parse_register(pinned(body), body)
        first, second = table["forms"]
        self.assertEqual((first["id"], len(first["legal_acts"])), (10, 2))
        self.assertEqual(first["legal_acts"][1][1], "Zmiana")
        self.assertEqual(second["values"][table["form_columns"].index("nazwa")], "")
        for index, rows in enumerate(
            [
                [HEADER, act_row(), form()],
                [HEADER, form(), [*act_row()[:6], "5.0", *act_row()[7:]]],
                [HEADER, form(), form()],
                [HEADER, form(fop_id="x1")],
                [HEADER, [*form()[:1], "OTHER.1", *form()[2:]]],
                [HEADER, form(area="12,5 ha")],
                [HEADER, form(), act_row(day="03.02.2001")],
                [HEADER, [*form()[:3], "tak", *form()[4:]]],
                [HEADER[::-1], form()],
                [HEADER, form(), [""] * len(HEADER), form("11")],
            ]
        ):
            changed = xlsx(rows)
            with self.subTest(index=index), self.assertRaises(FetchError):
                crfop.parse_register(pinned(changed), changed)
        with self.assertRaises(FetchError):
            crfop.parse_register({**pinned(body), "sha256": "0" * 64}, body)

    def fake_client(self, spec, body, licence="CC0 1.0"):
        responses = {
            f"{crfop.API}datasets/471": {
                "type": "dataset",
                "id": "471",
                "attributes": {"license_name": licence},
                "relationships": {"institution": {"data": {"type": "institution", "id": "23"}}},
            },
            f"{crfop.API}resources/{spec['resource']}": {
                "type": "resource",
                "id": str(spec["resource"]),
                "attributes": {
                    "link": spec["link"],
                    "file_size": spec["size"],
                    "format": "xls",
                    "title": spec["title"],
                    "contains_protected_data": False,
                    "description": "<p>Dane - stan na dzień 4 lutego\xa02026 r.</p>",
                },
                "relationships": {"dataset": {"data": {"type": "dataset", "id": "471"}}},
            },
        }

        class Client:
            def get(self, url):
                if url == spec["link"]:
                    return Response(200, body, {}, url)
                return Response(200, json.dumps({"data": responses[url]}).encode(), {}, url)

        return Client()

    def test_collect_parts_licence_and_profile(self):
        rows = [HEADER] + [form(str(n)) for n in range(1, 8)]
        body = xlsx(rows)
        spec = {**crfop.REGISTERS["uzytki-ekologiczne"], "size": len(body)}
        spec["sha256"] = hashlib.sha256(body).hexdigest()
        with patch.dict(crfop.REGISTERS, {"uzytki-ekologiczne": spec}):
            ids = []
            for part in (1, 2, 3):
                p = crfop.profile("uzytki-ekologiczne", part)
                channel = DataChannel(url=p["url"], region="PL", kind="table", config=p["config"])
                payload, _, summary = crfop.collect(channel, self.fake_client(spec, body))
                ids += [f["id"] for f in json.loads(payload)["forms"]]
                self.assertEqual(summary["forms_total_in_register"], 7)
                self.assertEqual(summary["units_unconfirmed"], ["powierzchnia"])
                self.assertTrue(summary["partial"])
            self.assertEqual(ids, list(range(1, 8)))
            with self.assertRaises(FetchError):
                crfop.collect(channel, self.fake_client(spec, body, licence="CC BY 4.0"))
            channel.config = {**channel.config, "part": 4}
            with self.assertRaises(FetchError):
                crfop.collect(channel, self.fake_client(spec, body))

    def test_configure_package_and_dispatch(self):
        release = RegistryRelease.objects.create(filename="t", sha256="d" * 64, sheets={})
        RegistryEntry.objects.create(
            registry_id=15, release=release, priority="P2", master={}, technical={}
        )
        call_command("configure_crfop", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_crfop", apply=True, stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.filter(kind="table", enabled=False).count(), 10)
        self.assertTrue(
            DataChannel.objects.filter(slug="crfop-uzytki-ekologiczne-czesc-3").exists()
        )
        call_command("configure_crfop", apply=True, stdout=io.StringIO())
        spec = crfop.REGISTERS["parki-narodowe"]
        with patch(
            "ingestion.data_service.Client",
            lambda: self.fake_client(spec, FIXTURE.read_bytes()),
        ):
            result = run_data("crfop-parki-narodowe", dry_run=True)[0]
        self.assertEqual((result["status"], result["records"]), ("dry_run", 23))
        changed = DataChannel.objects.get(slug="crfop-parki-narodowe")
        changed.config = {**changed.config, "admin": 1}
        changed.save()
        DataChannel.objects.filter(slug="crfop-rezerwaty-przyrody").delete()
        with self.assertRaises(CommandError):
            call_command("configure_crfop", apply=True, stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.filter(slug="crfop-rezerwaty-przyrody").exists())


class GiosNoise2023Tests(TestCase):
    def test_xls_day_serials_and_reader_selection(self):
        header = list(gt.NOISE_COLUMNS)
        row = {
            **dict.fromkeys(header, ""),
            "Województwo": "dolnośląskie",
            "Kod punktu pomiarowego": "D_02_000018_003",
            "Współrz. pp WGS84 (dł. geogr.)": "16.67475",
            "Współrz. pp WGS84 (szer. geogr.)": "50.374972",
            "Data rozpoczęcia": "45124",
            "Data zakończenia": "45125",
            "Czas odniesienia": "Dzień 16h",
            "Laeq po korekcie [dB]": "68.6",
        }
        spec = gt.RESOURCES[(gt.NOISE, 2023)][0]
        self.assertEqual(spec["format"], "xls")
        body = b"pinned"
        pinned_spec = {**spec, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        rows = [header, [row[name] for name in header]]
        with patch("ingestion.gios_tables.read_xls_sheet", return_value=rows) as reader:
            record = gt.parse_file(gt.NOISE, 2023, pinned_spec, body)["records"][0]
        reader.assert_called_once()
        self.assertEqual((record["start"], record["end"]), ("2023-07-17", "2023-07-18"))
        self.assertEqual(record["laeq_db"], 68.6)
        for value in ("45124.5", "2023-07-17", "44000"):
            changed = [header, [{**row, "Data rozpoczęcia": value}[name] for name in header]]
            with (
                self.subTest(value=value),
                patch("ingestion.gios_tables.read_xls_sheet", return_value=changed),
                self.assertRaises(FetchError),
            ):
                gt.parse_file(gt.NOISE, 2023, pinned_spec, body)
