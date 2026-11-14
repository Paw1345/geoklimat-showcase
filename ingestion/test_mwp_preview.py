import hashlib
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from ingestion import pig_mwp_tables as mwp
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.test_pig_mwp_tables import chem_row, level_row
from ingestion.transport import Response


class MwpPreviewTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="c" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            release=release, registry_id=63, master={}, technical={}, integration_state="x"
        )
        self.now = timezone.now()

    def copy(self, key="chemia", make=None, count=4):
        channel = DataChannel.objects.create(
            registry=self.registry,
            slug=mwp.slug(key),
            name=key,
            kind="table",
            region="PL",
            url=mwp.channel_url(key),
            config=mwp.config(key),
            attribution=mwp.ATTRIBUTION,
            access_approved=True,
            enabled=False,
        )
        spec = mwp.TABLES[key]
        make = make or (chem_row if key == "chemia" else level_row)
        client = Mock()
        responses = [
            {
                "name": spec["name"],
                "type": "Table",
                "fields": [{"name": name, "type": kind} for name, kind in spec["fields"].items()],
            },
            {"objectIdFieldName": "OBJECTID", "objectIds": list(range(1, count + 1))},
            {"count": count},
            {"features": [{"attributes": make(i)} for i in range(1, count + 1)]},
            {"objectIdFieldName": "OBJECTID", "objectIds": list(range(1, count + 1))},
            {"count": count},
        ]
        client.get.side_effect = lambda url: Response(
            200, json.dumps(responses.pop(0)).encode(), {}, url
        )
        payload, media, summary = mwp.collect(channel, client)
        snapshot = DataSnapshot.objects.create(
            channel=channel,
            payload=payload,
            media_type=media,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            summary=summary,
            expires_at=self.now + timedelta(days=30),
        )
        return channel, snapshot

    def page(self, channel):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            return self.client.get(reverse("data_detail", args=[channel.pk]))

    def change_data(self, snapshot, mutate):
        data = json.loads(bytes(snapshot.payload))
        mutate(data)
        payload = json.dumps(
            data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(
            payload=payload, size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()
        )

    def test_chemistry_decodes_three_rows_and_keeps_original_export(self):
        channel, snapshot = self.copy()
        before = DataSnapshot.objects.values().get(pk=snapshot.pk)
        response = self.page(channel)
        preview = response.context.get("mwp_preview")
        self.assertIsNotNone(preview, "Expected decoded MWP preview, not raw dictionaries")
        self.assertContains(response, "Zdekodowany podgląd MWP")
        data = json.loads(preview["text"])
        self.assertEqual(len(data["rows"]), 3)
        self.assertEqual([r["OBJECTID"] for r in data["rows"]], [1, 2, 3])
        self.assertEqual(data["rows"], [chem_row(i) for i in range(1, 4)])
        self.assertEqual(preview["snapshot"].pk, snapshot.pk)
        self.assertContains(response, snapshot.sha256)
        exported = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertEqual(exported.content, bytes(snapshot.payload))
        self.assertEqual(DataSnapshot.objects.values().get(pk=snapshot.pk), before)

    def test_levels_keep_units_null_negative_and_zero(self):
        def make(i):
            return {
                **level_row(i),
                "DATA_POMIARU": "2026-08-11",
                "GLEBOKOSC_ZWIERCIADLA": -4.51 if i == 1 else 0,
                "WYDAJNOSC": None if i == 1 else 0,
            }

        channel, _ = self.copy("pomiary", make)
        data = json.loads(self.page(channel).context["mwp_preview"]["text"])
        self.assertEqual(data["units"], mwp.TABLES["pomiary"]["units"])
        self.assertEqual(data["rows"], [make(i) for i in range(1, 4)])

    def test_original_whitespace_and_html_are_not_interpreted(self):
        channel, _ = self.copy(
            make=lambda i: {
                **chem_row(i),
                "JEDNOSTKA": " ",
                "WSKAZNIK": " <script>alert(1)</script> ",
            }
        )
        response = self.page(channel)
        data = json.loads(response.context["mwp_preview"]["text"])
        self.assertEqual(data["rows"][0]["JEDNOSTKA"], " ")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_changed_sha_refuses_without_raw_fallback(self):
        channel, snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(sha256="0" * 64)
        response = self.page(channel)
        self.assertContains(response, "Podgląd MWP niedostępny")
        self.assertIsNone(response.context["mwp_preview"])
        self.assertEqual(response.context["preview"], "")

    def test_invalid_later_rows_and_provenance_refuse_entire_preview(self):
        mutations = [
            lambda d: d["rows"][3].__setitem__(4, True),
            lambda d: d["rows"][3].__setitem__(4, -1),
            lambda d: d["rows"][3].__setitem__(4, 999),
            lambda d: d["rows"][3].__setitem__(8, 10**400),
            lambda d: d["rows"][3].__setitem__(8, True),
            lambda d: d["rows"][3].__setitem__(3, None),
            lambda d: d["rows"][3].__setitem__(0, 1),
            lambda d: d["rows"][3].append(None),
            lambda d: d.__setitem__("source_url", "https://example.org"),
            lambda d: d.__setitem__("period", ["2000-01-01", "2000-01-02"]),
            lambda d: d["dictionaries"].__setitem__("JEDNOSTKA", [2]),
            lambda d: d["columns"].reverse(),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                channel, snapshot = self.copy()
                self.change_data(snapshot, mutate)
                self.assertContains(self.page(channel), "Podgląd MWP niedostępny")
                channel.snapshots.all().delete()
                channel.delete()

    def test_approval_and_profile_refuse(self):
        for fields in (
            {"access_approved": False},
            {"region": "EU"},
            {"slug": "different"},
            {"attribution": "different"},
        ):
            with self.subTest(fields=fields):
                channel, _ = self.copy()
                DataChannel.objects.filter(pk=channel.pk).update(**fields)
                self.assertContains(self.page(channel), "Podgląd MWP niedostępny")
                channel.snapshots.all().delete()
                channel.delete()

    def test_byte_budget_is_checked_before_blob(self):
        channel, snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(size_bytes=1)
        with CaptureQueriesContext(connection) as queries:
            response = self.page(channel)
        self.assertContains(response, "Podgląd MWP niedostępny")
        self.assertFalse(
            any(
                '"ingestion_datasnapshot"."payload" FROM' in q["sql"]
                for q in queries.captured_queries
            )
        )
        DataSnapshot.objects.filter(pk=snapshot.pk).update(size_bytes=snapshot.size_bytes)
        with override_settings(DATA_AUTO_MAX_BYTES=100):
            self.assertContains(self.page(channel), "Podgląd MWP niedostępny")

    def test_missing_and_hard_expired_copy_are_explicit(self):
        channel, snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(fetched_at=self.now - timedelta(days=31))
        self.assertContains(self.page(channel), "Brak dostępnej kopii MWP")
        snapshot.delete()
        self.assertContains(self.page(channel), "Brak dostępnej kopii MWP")

    def test_output_budget_does_not_truncate_loq(self):
        channel, _ = self.copy(make=lambda i: {**chem_row(i), "ZNAK": "< LOQ " + "x" * 12000})
        self.assertContains(self.page(channel), "Podgląd MWP niedostępny")

    def test_malformed_summary_media_and_config_refuse_without_500(self):
        channel, snapshot = self.copy()
        for fields in (
            {"summary": []},
            {"summary": {**snapshot.summary, "records": True}},
            {"media_type": "text/html"},
            {"payload": b'{"schema":'},
        ):
            with self.subTest(fields=list(fields)):
                original = DataSnapshot.objects.values().get(pk=snapshot.pk)
                DataSnapshot.objects.filter(pk=snapshot.pk).update(**fields)
                self.assertContains(self.page(channel), "Podgląd MWP niedostępny")
                DataSnapshot.objects.filter(pk=snapshot.pk).update(**original)
        channel.config["layer"] = True
        channel.save(update_fields=["config"])
        self.assertContains(self.page(channel), "Podgląd MWP niedostępny")
        DataChannel.objects.filter(pk=channel.pk).update(config=[])
        self.assertContains(self.page(channel), "Podgląd MWP niedostępny")

    def test_payload_change_between_byte_check_and_read_refuses(self):
        channel, snapshot = self.copy()
        from django.db.models.query import QuerySet

        original = QuerySet.values_list

        def changed(queryset, *fields, **kwargs):
            if fields == ("payload",):
                DataSnapshot.objects.filter(pk=snapshot.pk).update(payload=b"x" * 9000)
            return original(queryset, *fields, **kwargs)

        with patch.object(QuerySet, "values_list", changed):
            self.assertContains(self.page(channel), "Podgląd MWP niedostępny")

    def test_valid_sha_but_noncanonical_json_refuses(self):
        channel, snapshot = self.copy()
        payload = json.dumps(json.loads(bytes(snapshot.payload)), ensure_ascii=False).encode()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(
            payload=payload, size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()
        )
        self.assertContains(self.page(channel), "Podgląd MWP niedostępny")

    def test_changed_mode_does_not_fall_back_to_raw_mwp_json(self):
        channel, _ = self.copy()
        DataChannel.objects.filter(pk=channel.pk).update(config={})
        response = self.page(channel)
        self.assertTrue(
            response.context["mwp_error"], "Changed mode must refuse, not reveal raw JSON"
        )
        self.assertEqual(response.context["preview"], "")
