import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ingestion import pig_mwp_tables as mwp
from ingestion.data_service import collect, run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response


def chem_row(i):
    below = i % 3 == 0
    return {
        "OBJECTID": i,
        "OTWOR_ID": 800 + i % 7,
        "BAZA_HISTORYCZNA": "PSG",
        "DATA_POBORU": "2025-10-0" + str(1 + i % 9),
        "OZNACZENIE": "oznaczenie laboratoryjne",
        "WSKAZNIK": ["Brom", "Krzemionka", " Chrom"][i % 3],
        "SYMBOL_CHEMICZNY": ["Br", "SiO2", "Cr"][i % 3],
        "ZNAK": "< 0.1 (wartość poniżej granicy oznaczalności LOQ)" if below else None,
        "WARTOSC": None if below else 12.1,
        "JEDNOSTKA": "mg/dm3",
    }


def level_row(i):
    return {
        "OBJECTID": i,
        "OTWOR_ID": 9000 + i % 50,
        "DATA_POMIARU": " 2026-08-" + str(10 + i % 20),
        "GLEBOKOSC_ZWIERCIADLA": -4.51 if i == 7 else 7.11,
        "WYDAJNOSC": None,
    }


class MwpTableTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="c" * 64, sheets={})
        RegistryEntry.objects.create(
            release=release, registry_id=63, master={}, technical={}, integration_state="x"
        )
        self.ids = {"pomiary": list(range(1, 2501)), "chemia": list(range(5, 1205))}

    def channel(self, key="pomiary", **extra):
        return DataChannel.objects.create(
            registry=RegistryEntry.objects.get(registry_id=63),
            slug=mwp.slug(key),
            name=key,
            kind="table",
            region="PL",
            url=mwp.channel_url(key),
            config=mwp.config(key),
            **extra,
        )

    def http(self, key="pomiary", schema=None, rows=None):
        spec = mwp.TABLES[key]
        make = rows or (level_row if key == "pomiary" else chem_row)
        http = Mock()

        def get(url):
            q = parse_qs(urlsplit(url).query)
            ids = self.ids[key]
            if not urlsplit(url).path.endswith("/query"):
                data = schema or {
                    "name": spec["name"],
                    "type": "Table",
                    "fields": [{"name": n, "type": t} for n, t in spec["fields"].items()],
                }
            elif q.get("returnIdsOnly") == ["true"]:
                data = {"objectIdFieldName": "OBJECTID", "objectIds": list(reversed(ids))}
            elif q.get("returnCountOnly") == ["true"]:
                data = {"count": len(ids)}
            else:
                offset = int(q["resultOffset"][0])
                page = ids[offset : offset + int(q["resultRecordCount"][0])]
                data = {
                    "features": [{"attributes": make(i)} for i in page],
                    "exceededTransferLimit": offset + len(page) < len(ids),
                }
            return Response(200, json.dumps(data).encode(), {}, url)

        http.get.side_effect = get
        return http

    def test_full_read_pins_identities_and_keeps_values(self):
        payload, media, summary = collect(self.channel(), self.http())
        data = json.loads(payload)
        self.assertEqual((summary["records"], summary["source_count"]), (2500, 2500))
        self.assertEqual(
            (summary["period_start"], summary["period_end"]), ("2026-08-10", "2026-08-29")
        )
        self.assertEqual(summary["stations"], 50)
        self.assertEqual(data["columns"][0], "OBJECTID")
        self.assertEqual(data["rows"][6], [7, 9007, "2026-08-17", -4.51, None])
        self.assertEqual(data["units"]["GLEBOKOSC_ZWIERCIADLA"], "m p.p.t.")
        self.assertIsNone(data["source_generated_at"])
        self.assertEqual(collect(self.channel("chemia"), self.http("chemia"))[2]["records"], 1200)

    def test_chemistry_dictionary_is_lossless(self):
        payload, _, summary = collect(self.channel("chemia"), self.http("chemia"))
        data = json.loads(payload)
        columns, dictionaries = data["columns"], data["dictionaries"]
        for row, identity in zip(data["rows"], self.ids["chemia"], strict=True):
            decoded = {
                name: (
                    dictionaries[name][item] if name in dictionaries and item is not None else item
                )
                for name, item in zip(columns, row, strict=True)
            }
            original = chem_row(identity)
            original["DATA_POBORU"] = original["DATA_POBORU"].strip()
            self.assertEqual(decoded, original)
        self.assertEqual(dictionaries["WSKAZNIK"], [" Chrom", "Brom", "Krzemionka"])

    def test_store_then_repeat_unchanged(self):
        self.channel(enabled=True, access_approved=True)
        with patch("ingestion.data_service.Client", lambda: self.http()):
            first = run_data(mwp.slug("pomiary"), force=True)[0]
            second = run_data(mwp.slug("pomiary"), force=True)[0]
        self.assertEqual((first["status"], second["status"]), ("stored", "unchanged"))
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_historical_tables_explain_copy_age_and_keep_export_unchanged(self):
        for key in ("pomiary", "chemia"):
            with self.subTest(table=key):
                channel = self.channel(key, access_approved=True, enabled=False)
                payload, media, summary = collect(channel, self.http(key))
                snapshot = DataSnapshot.objects.create(
                    channel=channel,
                    expires_at=timezone.now() + timedelta(days=30),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    media_type=media,
                    payload=payload,
                    summary=summary,
                )
                before = DataSnapshot.objects.filter(pk=snapshot.pk).values().get()
                with patch("ingestion.transport.Client.get") as http:
                    response = self.client.get(reverse("data_detail", args=[channel.pk]))
                    self.assertContains(response, "Lokalne kopie tabel MWP")
                    self.assertContains(response, "od pobrania, nie wieku pomiarów")
                    self.assertContains(response, summary["period_start"])
                    self.assertContains(response, summary["period_end"])
                    self.assertNotContains(response, "wykluczają obserwacje starsze niż 30 dni")
                    download = self.client.get(reverse("data_download", args=[snapshot.pk]))
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.content, payload)
                    http.assert_not_called()
                self.assertEqual(DataSnapshot.objects.filter(pk=snapshot.pk).values().get(), before)

    def test_other_timed_channels_keep_observation_retention_description(self):
        channel = self.channel()
        channel.kind = "imgw"
        channel.config = {}
        channel.save()
        with patch("ingestion.transport.Client.get") as http:
            response = self.client.get(reverse("data_detail", args=[channel.pk]))
            self.assertContains(response, "wykluczają obserwacje starsze niż 30 dni")
            self.assertNotContains(response, "Lokalne kopie tabel MWP")
            http.assert_not_called()

    def test_manifest_change_during_read_stores_nothing(self):
        http = self.http()
        get = http.get.side_effect
        calls = {"ids": 0}

        def drift(url):
            if parse_qs(urlsplit(url).query).get("returnIdsOnly"):
                calls["ids"] += 1
                if calls["ids"] == 2:
                    self.ids["pomiary"] = self.ids["pomiary"][:-1] + [9999]
            return get(url)

        http.get.side_effect = drift
        self.channel(enabled=True, access_approved=True)
        with patch("ingestion.data_service.Client", lambda: http):
            result = run_data(mwp.slug("pomiary"), force=True)[0]
        self.assertEqual(result["status"], "failed")
        self.assertFalse(DataSnapshot.objects.exists())

    def test_refusals(self):
        channel = self.channel()
        spec = mwp.TABLES["pomiary"]
        extra = {
            "name": spec["name"],
            "type": "Table",
            "fields": [{"name": n, "type": t} for n, t in spec["fields"].items()]
            + [{"name": "UWAGI", "type": "esriFieldTypeString"}],
        }
        cases = [
            self.http(schema=extra),
            self.http(rows=lambda i: {**level_row(i), "DATA_POMIARU": "31.08.2026"}),
            self.http(rows=lambda i: {**level_row(i), "OBJECTID": i + 1}),
            self.http(rows=lambda i: {**level_row(i), "WYDAJNOSC": "1,5"}),
        ]
        for http in cases:
            with self.assertRaises(FetchError):
                collect(channel, http)
        channel.url = mwp.channel_url("chemia")
        http = self.http()
        with self.assertRaises(FetchError):
            collect(channel, http)
        http.get.assert_not_called()

    @override_settings(DATA_AUTO_MAX_BYTES=1000)
    def test_payload_budget(self):
        with self.assertRaises(FetchError):
            collect(self.channel(), self.http())

    def test_configuration_dry_run_idempotence_preserves_admin(self):
        out = io.StringIO()
        call_command("configure_pig_mwp_tables", stdout=out)
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_pig_mwp_tables", apply=True, stdout=out)
        call_command("configure_pig_mwp_tables", apply=True, stdout=out)
        self.assertEqual(DataChannel.objects.filter(kind="table", enabled=False).count(), 2)
        c = DataChannel.objects.get(slug=mwp.slug("chemia"))
        c.url = "https://example.org/"
        c.save()
        with self.assertRaises(CommandError):
            call_command("configure_pig_mwp_tables", apply=True, stdout=out)
