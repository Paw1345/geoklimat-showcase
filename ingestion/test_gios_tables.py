import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ingestion import gios_tables as gt
from ingestion.data_service import run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.test_gios_major_accidents import xlsx
from ingestion.transport import FetchError, Response

GW_HEADER = [
    "L.p.",
    "Identyfikator UE punktu pomiarowego",
    "PUWG 1992 X",
    "Województwo",
    "Rok badań",
    "Data poboru próbki",
    "Arsen [mgAs/l]",
    "Odczyn pH - wartość terenowa",
    "Benzen [µg/l]",
]
NOISE_HEADER = list(gt.NOISE_COLUMNS)


def gw_csv(rows, header=GW_HEADER):
    lines = [",".join(header)] + [",".join(f'"{cell}"' for cell in row) for row in rows]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def gw_row(number="1", day="2024-05-07", values=("<0,002", "7,14", "n.o.")):
    return [number, "PL60001_014", "185897,43", "mazowieckie", "2024", day, *values]


def noise_csv(rows, delimiter=",", encoding="utf-8", header=NOISE_HEADER):
    lines = [delimiter.join(header)] + [delimiter.join(f'"{cell}"' for cell in row) for row in rows]
    return ("\n".join(lines) + "\n").encode(encoding)


def noise_row(start="2022-05-17 06:00:00", end="2022-05-18 06:00:00", **changes):
    row = {
        "Województwo": "dolnośląskie",
        "Miejscowość": "Chrząstawa Mała",
        "Kod punktu pomiarowego": "D_02_000510_001",
        "Nazwa punktu pomiarowego": "ul. Wrocławska 79",
        "Współrz. pp WGS84 (dł. geogr.)": "17,281667",
        "Współrz. pp WGS84 (szer. geogr.)": "51,081778",
        "Cel pomiaru": "Państwowy monitoring środowiska, art. 26 Poś",
        "Data rozpoczęcia": start,
        "Data zakończenia": end,
        "Nazwa odcinka drogi": "droga powiatowa",
        "Czas odniesienia": "Dzień 16h",
        "Laeq po korekcie [dB]": "64,7",
        **changes,
    }
    return [row[name] for name in NOISE_HEADER]


def pinned(spec, body, **changes):
    return {
        **spec,
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        **changes,
    }


GW_SPEC = gt.RESOURCES[(gt.GW, 2024)][0]
NOISE_SPEC = gt.RESOURCES[(gt.NOISE, 2022)][0]


class GiosTablesTests(TestCase):
    def test_noise_2024_detail_and_export_distinguish_observation_age_from_retention(self):
        release = RegistryRelease.objects.create(filename="t", sha256="c" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=48, release=release, priority="P2", master={}, technical={}
        )
        expected = gt.profile(gt.NOISE, 2024)
        channel = DataChannel.objects.create(
            registry=registry,
            slug=expected["slug"],
            kind="table",
            region="PL",
            url=expected["url"],
            config=expected["config"],
            attribution=expected["attribution"],
            access_approved=True,
        )
        body = json.dumps({"year": 2024, "records": [{"start": "2024-01-01"}]}).encode()
        snapshot = DataSnapshot.objects.create(
            channel=channel,
            expires_at=timezone.now() + timedelta(days=30),
            payload=body,
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            media_type="application/json",
            summary={"collector": "gios_tables", "year": 2024, "records": 1},
        )
        response = self.client.get(reverse("data_detail", args=[channel.pk]))
        self.assertContains(response, "Lokalne kopie tabel rocznych")
        self.assertContains(response, "Retencja 30 dni dotyczy lokalnej kopii, nie wieku wyników")
        self.assertNotContains(response, "wykluczają obserwacje starsze niż 30 dni")
        download = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, body)
        channel.kind = "imgw"
        channel.config = {}
        channel.save()
        DataSnapshot.objects.filter(pk=snapshot.pk).delete()
        self.assertContains(
            self.client.get(reverse("data_detail", args=[channel.pk])),
            "wykluczają obserwacje starsze niż 30 dni",
        )

    def test_noise_2024_profile_pins_existing_on_demand_resource(self):
        expected = gt.profile(gt.NOISE, 2024)
        self.assertEqual(expected["slug"], "gios-noise-road-2024")
        self.assertEqual(expected["registry_id"], 48)
        self.assertEqual(
            expected["config"], {"mode": "gios_tables", "product": "noise_road", "year": 2024}
        )
        spec = gt.RESOURCES[(gt.NOISE, 2024)][0]
        self.assertEqual((spec["resource"], spec["format"], spec["size"]), (989426, "xls", 754688))
        self.assertEqual(
            spec["sha256"], "1104c27c78ac16a821ece3c4958421098d7ba55ee209ce87a88f75707d279a7f"
        )

    def test_noise_2024_day_serials_reordered_header_and_source_identity(self):
        spec = gt.RESOURCES[(gt.NOISE, 2024)][0]
        body = b"reviewed-xls"
        header = list(reversed(NOISE_HEADER))
        row = dict(zip(NOISE_HEADER, noise_row("45292", "45657"), strict=True))
        rows = [header, [row[name] for name in header], [row[name] for name in header]]
        with patch("ingestion.gios_tables.read_xls_sheet", return_value=rows):
            table = gt.parse_file(gt.NOISE, 2024, pinned(spec, body), body)
        first, second = table["records"]
        self.assertEqual((first["start"], first["end"]), ("2024-01-01", "2024-12-31"))
        self.assertEqual((first["id"], second["id"]), ("989426:1", "989426:2"))
        self.assertEqual(first["laeq_db"], 64.7)
        self.assertEqual(table["parameters"][0]["unit"], "dB")
        self.assertEqual(table["date_precision"], "day")
        self.assertEqual(table["source_timezone"], "UNCONFIRMED")
        for value in ("45292.5", "2024-01-01", "45124"):
            changed = [header, [{**row, "Data rozpoczęcia": value}[name] for name in header]]
            with (
                self.subTest(value=value),
                patch("ingestion.gios_tables.read_xls_sheet", return_value=changed),
                self.assertRaises(FetchError),
            ):
                gt.parse_file(gt.NOISE, 2024, pinned(spec, body), body)
        changed = [header, [{**row, "Laeq po korekcie [dB]": "<40"}[name] for name in header]]
        with (
            patch("ingestion.gios_tables.read_xls_sheet", return_value=changed),
            self.assertRaises(FetchError),
        ):
            gt.parse_file(gt.NOISE, 2024, pinned(spec, body), body)

    def test_noise_2024_collects_historical_table_with_source_receipt(self):
        spec = gt.RESOURCES[(gt.NOISE, 2024)][0]
        body = b"reviewed-xls"
        spec = pinned(spec, body)
        expected = gt.profile(gt.NOISE, 2024)
        channel = DataChannel(
            url=expected["url"], region="PL", kind="table", config=expected["config"]
        )
        rows = [NOISE_HEADER, noise_row("45292", "45657")]
        with (
            patch.dict(gt.RESOURCES, {(gt.NOISE, 2024): [spec]}),
            patch("ingestion.gios_tables.read_xls_sheet", return_value=rows),
        ):
            payload, media, summary = gt.collect(
                channel, self.fake_client(gt.NOISE, {_Spec(spec): body})
            )
        data = json.loads(payload)
        self.assertEqual(media, "application/json")
        self.assertEqual((data["year"], summary["records"], summary["rejected"]), (2024, 1, 0))
        self.assertFalse(summary["new_api_used"])
        self.assertEqual(data["tables"][0]["sha256"], spec["sha256"])

    def test_groundwater_units_qualifiers_codes_and_lp_identity(self):
        body = gw_csv([gw_row("2", values=("0,005", "", "1.5E-3")), gw_row("1")])
        spec = pinned(GW_SPEC, body, format="csv")
        table = gt.parse_file(gt.GW, 2024, spec, body)
        units = {p["name"]: (p["unit"], p["unit_status"]) for p in table["parameters"]}
        self.assertEqual(units["Arsen"], ("mgAs/l", "source_header"))
        self.assertEqual(units["Benzen"], ("µg/l", "source_header"))
        self.assertEqual(units["Odczyn pH - wartość terenowa"], (None, "UNCONFIRMED"))
        first, second = table["records"]
        self.assertEqual(
            (first["id"], second["id"]), (f"{GW_SPEC['resource']}:1", f"{GW_SPEC['resource']}:2")
        )
        self.assertEqual(
            first["results"], [{"qualifier": "<", "value": 0.002}, 7.14, {"code": "n.o."}]
        )
        self.assertEqual(second["results"], [0.005, None, 0.0015])
        self.assertEqual(first["date"], "2024-05-07")
        self.assertEqual(table["site_columns"][0], "Identyfikator UE punktu pomiarowego")
        for index, rows in enumerate(
            [
                [gw_row("1"), gw_row("3")],
                [gw_row("1"), gw_row("1")],
                [gw_row("1", day="2023-05-07")],
                [gw_row("1", values=("abc", "7", "1"))],
                [gw_row("1"), [""] * 9, gw_row("2")],
                [gw_row("1") + ["extra"]],
            ]
        ):
            changed = gw_csv(rows)
            with self.subTest(index=index), self.assertRaises(FetchError):
                gt.parse_file(gt.GW, 2024, pinned(GW_SPEC, changed, format="csv"), changed)
        with self.assertRaises(FetchError):
            gt.parse_file(gt.GW, 2024, {**spec, "sha256": "0" * 64}, body)

    def test_groundwater_xlsx_day_serial_and_reviewed_formula_columns(self):
        rows = [GW_HEADER, gw_row("1", day="45418")]
        body = xlsx(rows)
        record = gt.parse_file(gt.GW, 2024, pinned(GW_SPEC, body, format="xlsx"), body)
        self.assertEqual(record["records"][0]["date"], "2024-05-06")
        formula = xlsx(rows, formula="<f>1+1</f>")
        columns = set("ABCDEFGHI")
        spec = pinned(GW_SPEC, formula, format="xlsx", formula_columns=columns)
        self.assertEqual(len(gt.parse_file(gt.GW, 2024, spec, formula)["records"]), 1)
        with self.assertRaises(FetchError):
            gt.parse_file(gt.GW, 2024, {**spec, "formula_columns": {"LF"}}, formula)

    def test_noise_formats_null_level_periods_and_rejections(self):
        body = noise_csv([noise_row(), noise_row(**{"Laeq po korekcie [dB]": ""})])
        table = gt.parse_file(gt.NOISE, 2022, pinned(NOISE_SPEC, body), body)
        first, second = table["records"]
        self.assertEqual((first["laeq_db"], second["laeq_db"]), (64.7, None))
        self.assertEqual((first["start"], first["end"]), ("2022-05-17T06:00", "2022-05-18T06:00"))
        self.assertEqual(table["parameters"][0]["unit"], "dB")
        self.assertEqual(table["source_timezone"], "UNCONFIRMED")
        cp1250 = noise_csv([noise_row("27.08.2020 06:00", "30.08.2020 06:00")], ";", "cp1250")
        spec2020 = pinned(gt.RESOURCES[(gt.NOISE, 2020)][0], cp1250)
        self.assertEqual(
            gt.parse_file(gt.NOISE, 2020, spec2020, cp1250)["records"][0]["voivodeship"],
            "dolnośląskie",
        )
        for index, row in enumerate(
            [
                noise_row(**{"Czas odniesienia": "Rano 2h"}),
                noise_row(**{"Kod punktu pomiarowego": "PPH01"}),
                noise_row(**{"Współrz. pp WGS84 (dł. geogr.)": "30,1"}),
                noise_row("2022-05-18 06:00:00", "2022-05-17 06:00:00"),
                noise_row("17.05.2022 06:00", "18.05.2022 06:00"),
                noise_row(**{"Laeq po korekcie [dB]": "<40"}),
            ]
        ):
            changed = noise_csv([row])
            with self.subTest(index=index), self.assertRaises(FetchError):
                gt.parse_file(gt.NOISE, 2022, pinned(NOISE_SPEC, changed), changed)

    def test_noise_reviewed_shifted_row_is_reported_not_realigned(self):
        good = noise_row("4.11.2021", "5.11.2021")
        shifted = good[:3] + [" pora dnia"] + good[3:]
        spec2021 = gt.RESOURCES[(gt.NOISE, 2021)][0]
        body = noise_csv([good, shifted, good], header=NOISE_HEADER + [""])
        spec = pinned(spec2021, body, rejected_rows={2: "przesunięty"})
        table = gt.parse_file(gt.NOISE, 2021, spec, body)
        self.assertEqual([r["source_row_number"] for r in table["records"]], [1, 3])
        self.assertEqual(
            table["rejected_rows"], [{"source_row_number": 2, "reason": "przesunięty"}]
        )
        self.assertEqual(table["date_precision"], "day")
        fixed = noise_csv([good, good, good], header=NOISE_HEADER + [""])
        with self.assertRaises(FetchError):
            gt.parse_file(gt.NOISE, 2021, pinned(spec2021, fixed, rejected_rows={2: "x"}), fixed)
        with self.assertRaises(FetchError):
            gt.parse_file(gt.NOISE, 2021, pinned(spec2021, body, rejected_rows={}), body)

    def fake_client(self, product, files, licence="CC BY 4.0"):
        dataset = gt.DATASETS[product]
        responses = {
            f"{gt.API}datasets/{dataset}": {
                "type": "dataset",
                "id": str(dataset),
                "attributes": {"license_name": licence, "license_condition_default_cc40": True},
                "relationships": {"institution": {"data": {"type": "institution", "id": "100"}}},
            }
        }
        for spec in files:
            responses[f"{gt.API}resources/{spec['resource']}"] = {
                "type": "resource",
                "id": str(spec["resource"]),
                "attributes": {
                    "link": spec["link"],
                    "file_size": spec["size"],
                    "format": spec["format"],
                    "title": spec["title"],
                    "contains_protected_data": False,
                },
                "relationships": {"dataset": {"data": {"type": "dataset", "id": str(dataset)}}},
            }
        bodies = {spec["link"]: body for spec, body in files.items()}

        class Client:
            def get(self, url):
                if url in bodies:
                    return Response(200, bodies[url], {}, url)
                return Response(200, json.dumps({"data": responses[url]}).encode(), {}, url)

        return Client()

    def noise_files(self):
        body = noise_csv([noise_row(), noise_row(**{"Czas odniesienia": "Noc 8h"})])
        spec = pinned(NOISE_SPEC, body)
        return {(gt.NOISE, 2022): [spec]}, {_key(spec): body}

    def test_collect_pins_profile_catalogue_and_budget(self):
        resources, files = self.noise_files()
        spec = resources[(gt.NOISE, 2022)][0]
        expected = gt.profile(gt.NOISE, 2022)
        channel = DataChannel(
            url=expected["url"], region="PL", kind="table", config=expected["config"]
        )
        with patch.dict(gt.RESOURCES, resources):
            client = self.fake_client(gt.NOISE, {_Spec(spec): files[_key(spec)]})
            payload, media, summary = gt.collect(channel, client)
            data = json.loads(payload)
            self.assertEqual((summary["records"], summary["rejected"]), (2, 0))
            self.assertEqual(summary["units_unconfirmed"], [])
            self.assertEqual(data["tables"][0]["sha256"], spec["sha256"])
            with self.assertRaises(FetchError):
                gt.collect(
                    channel,
                    self.fake_client(
                        gt.NOISE, {_Spec(spec): files[_key(spec)]}, licence="CC BY-NC 4.0"
                    ),
                )
            channel.config = {**expected["config"], "year": 2017}
            with self.assertRaises(FetchError):
                gt.collect(channel, client)

    def test_configure_package_idempotent_protected_and_dispatched(self):
        release = RegistryRelease.objects.create(filename="t", sha256="c" * 64, sheets={})
        for key in (47, 48):
            RegistryEntry.objects.create(
                registry_id=key, release=release, priority="P2", master={}, technical={}
            )
        file_channel = DataChannel.objects.create(
            registry=RegistryEntry.objects.get(registry_id=48),
            slug="gios-noise-road-2024-file",
            name="Existing file",
            kind="file",
            region="PL",
            url="https://example.test/preserved.xls",
            config={"administrator": "preserved"},
            enabled=False,
            access_approved=True,
        )
        call_command("configure_gios_tables", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.filter(kind="table").exists())
        call_command("configure_gios_tables", apply=True, stdout=io.StringIO())
        table_2024 = DataChannel.objects.get(slug="gios-noise-road-2024")
        self.assertEqual(table_2024.registry.registry_id, 48)
        self.assertFalse(table_2024.enabled)
        self.assertFalse(table_2024.access_approved)
        file_channel.refresh_from_db()
        self.assertEqual(file_channel.url, "https://example.test/preserved.xls")
        self.assertEqual(file_channel.config, {"administrator": "preserved"})
        self.assertTrue(file_channel.access_approved)
        self.assertFalse(file_channel.enabled)
        self.assertEqual(DataChannel.objects.filter(kind="table").count(), len(gt.RESOURCES))
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())
        self.assertEqual(
            set(
                DataChannel.objects.filter(kind="table").values_list("interval_minutes", flat=True)
            ),
            {1440},
        )
        call_command("configure_gios_tables", apply=True, stdout=io.StringIO())
        resources, files = self.noise_files()
        spec = resources[(gt.NOISE, 2022)][0]
        with (
            patch.dict(gt.RESOURCES, resources),
            patch(
                "ingestion.data_service.Client",
                lambda: self.fake_client(gt.NOISE, {_Spec(spec): files[_key(spec)]}),
            ),
        ):
            result = run_data("gios-noise-road-2022", dry_run=True)[0]
        self.assertEqual((result["status"], result["records"]), ("dry_run", 2))
        DataChannel.objects.filter(slug="gios-groundwater-2019").update(interval_minutes=60)
        DataChannel.objects.filter(slug="gios-noise-road-2018").delete()
        changed = DataChannel.objects.get(slug="gios-groundwater-2020")
        changed.config = {**changed.config, "admin": True}
        changed.save()
        with self.assertRaises(CommandError):
            call_command("configure_gios_tables", apply=True, stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.filter(slug="gios-noise-road-2018").exists())


def _key(spec):
    return spec["link"]


class _Spec(dict):
    """Hashable spec wrapper for the fake client map."""

    def __hash__(self):
        return hash(self["link"])
