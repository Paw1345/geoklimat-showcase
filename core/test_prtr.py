import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from ingestion import gios_prtr_table as profile
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.prtr_releases import parse_releases
from ingestion.test_prtr_releases import fixture


class PrtrSearchTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="synthetic", sha256="1" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=49, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=profile.SLUG,
            kind="table",
            url=profile.URL,
            region="PL",
            config=profile.CONFIG.copy(),
            attribution=profile.ATTRIBUTION,
            access_approved=True,
        )
        self.url = f"/data/{self.channel.pk}/prtr/"
        self.row, self.parsed = parse_releases(fixture())

    def copy(self, rows=None, change=None):
        records = self.row if rows is None else rows
        summary = {
            **self.parsed,
            "source_sha256": profile.original.SHA256,
            "source_bytes": profile.original.SIZE,
            "product_scope": self.parsed["scope"],
            "scope": "PL",
            "kind": "gios_prtr_releases",
            "collector": "gios_prtr_releases",
            "schema": profile.CONFIG["schema"],
            "source_url": profile.URL,
            "records": len(records),
            "partial": True,
        }
        data = {
            "records": records,
            "provenance": summary.copy(),
            "attribution": profile.ATTRIBUTION,
        }
        if change:
            change(data)
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        return DataSnapshot.objects.create(
            channel=self.channel,
            payload=payload,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type="application/json",
            summary=summary,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def test_default_public_table_keeps_text_null_and_does_not_fetch_or_write(self):
        row = {**self.row[0], "total_quantity": "100.00", "accidental_quantity": None}
        snapshot = self.copy([row])
        before = list(DataSnapshot.objects.values())
        with patch("ingestion.transport.Client.get") as get:
            response = self.client.get(self.url)
            get.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100.00")
        self.assertContains(response, "brak danych")
        self.assertContains(response, "2017")
        self.assertContains(response, "kg/rok")
        self.assertContains(response, f"snapshot={snapshot.pk}")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(before, list(DataSnapshot.objects.values()))

    def test_reader_verifies_copy_without_changing_payload_or_year(self):
        from ingestion.prtr_table_read import read_copy

        snapshot = self.copy()
        result = read_copy(self.channel, snapshot.pk, timezone.now())
        self.assertEqual(result["records"], self.row)
        self.assertEqual(result["snapshot"].sha256, snapshot.sha256)
        self.assertEqual(result["summary"]["reporting_year"], 2017)

    def test_filters_combine_casefold_name_or_id_exact_pollutant_and_medium(self):
        self.copy(
            [
                {**self.row[0], "facility_id": "A-01", "facility_name": "Łódź Alfa"},
                {
                    **self.row[0],
                    "facility_id": "A-02",
                    "facility_name": "Łódź Beta",
                    "medium_code": "WATER",
                },
                {**self.row[0], "facility_id": "A-03", "pollutant_code": "CO"},
            ]
        )
        response = self.client.get(
            self.url, {"facility": "ŁÓDŹ", "pollutant": "co2", "medium": "AIR"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["facility_id"] for r in response.context["page"]], ["A-01"])
        response = self.client.get(self.url, {"facility": "a-02", "medium": "WATER"})
        self.assertEqual([r["facility_id"] for r in response.context["page"]], ["A-02"])

    def test_pagination_pins_copy_and_filters_when_new_copy_appears(self):
        rows = [{**self.row[0], "facility_id": f"F-{n:03}"} for n in reversed(range(120))]
        first = self.copy(rows)
        response = self.client.get(self.url, {"pollutant": "CO2", "medium": "AIR"})
        self.assertEqual(len(response.context["page"]), 50)
        self.assertEqual(response.context["page"][0]["facility_id"], "F-000")
        next_link = response.context["links"]["next"]
        self.assertIn(f"snapshot={first.pk}", next_link)
        self.assertIn("pollutant=CO2", next_link)
        self.assertIn("medium=AIR", next_link)
        self.copy([{**self.row[0], "facility_id": "NEW"}])
        response = self.client.get(self.url + next_link)
        self.assertEqual(response.context["copy"]["snapshot"].pk, first.pk)
        self.assertEqual(response.context["page"][0]["facility_id"], "F-050")
        self.assertEqual(len(response.context["page"]), 50)

    def test_invalid_and_duplicate_or_unknown_parameters_fail_without_records(self):
        self.copy()
        for query in (
            "?page=0",
            "?page=-1",
            "?page=101",
            "?page=x",
            "?page=2",
            "?medium=SEA",
            "?facility=" + "a" * 101,
            "?pollutant=" + "b" * 101,
            "?snapshot=0",
            "?snapshot=" + "9" * 30,
            "?year=2018",
            "?page=1&page=2",
        ):
            with self.subTest(query=query):
                response = self.client.get(self.url + query)
                self.assertEqual(response.status_code, 400)
                self.assertNotContains(response, "Public facility", status_code=400)

    def test_empty_copy_and_no_matches_are_distinct_states(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Brak aktualnej kopii")
        self.copy()
        response = self.client.get(self.url, {"facility": "nonexistent"})
        self.assertContains(response, "Brak dopasowań")
        self.assertNotContains(response, "Brak aktualnej kopii")

    def test_hard_expiry_and_wrong_snapshot_do_not_fall_back_to_latest(self):
        first = self.copy()
        self.copy([{**self.row[0], "facility_id": "NEW"}])
        DataSnapshot.objects.filter(pk=first.pk).update(
            fetched_at=timezone.now() - timedelta(days=31)
        )
        response = self.client.get(self.url, {"snapshot": first.pk})
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "NEW", status_code=404)
        self.assertEqual(self.client.get(self.url, {"snapshot": 999999}).status_code, 404)

    def test_corrupt_payload_metadata_and_shape_fail_closed(self):
        for change in (
            lambda d: d["provenance"].update(reporting_year=2018),
            lambda d: d["records"][0].update(unit="tonnes/year"),
            lambda d: d["records"][0].update(contact="private"),
            lambda d: d["records"][0].update(facility_name=42),
        ):
            with self.subTest(change=change):
                snapshot = self.copy([self.row[0].copy()], change)
                response = self.client.get(self.url, {"snapshot": snapshot.pk})
                self.assertEqual(response.status_code, 409)
                self.assertNotContains(response, "Public facility", status_code=409)
        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(sha256="0" * 64)
        self.assertEqual(self.client.get(self.url, {"snapshot": snapshot.pk}).status_code, 409)

    def test_oversize_and_expired_copy_are_not_loaded_or_displayed(self):
        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(size_bytes=6 * 1024 * 1024)
        self.assertEqual(self.client.get(self.url, {"snapshot": snapshot.pk}).status_code, 409)
        DataSnapshot.objects.filter(pk=snapshot.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self.client.get(self.url, {"snapshot": snapshot.pk}).status_code, 404)

    def test_profile_or_approval_changes_and_other_channels_are_rejected(self):
        self.copy()
        for field, value in (("access_approved", False), ("region", "EU"), ("kind", "file")):
            with self.subTest(field=field):
                before = getattr(self.channel, field)
                setattr(self.channel, field, value)
                self.channel.save()
                self.assertEqual(self.client.get(self.url).status_code, 404)
                setattr(self.channel, field, before)
                self.channel.save()
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_noncanonical_duplicate_and_nonfinite_json_fail_with_valid_hash(self):
        snapshot = self.copy()
        original = bytes(snapshot.payload)
        for payload in (
            b" " + original,
            original.replace(b'"records":', b'"records":[],"records":', 1),
            original.replace(b'"total_quantity":"100.00"', b'"total_quantity":NaN'),
        ):
            with self.subTest(payload=payload[:30]):
                self.assertNotEqual(payload, original)
                DataSnapshot.objects.filter(pk=snapshot.pk).update(
                    payload=payload,
                    size_bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                )
                self.assertEqual(
                    self.client.get(self.url, {"snapshot": snapshot.pk}).status_code, 409
                )

    def test_actual_size_mismatch_rejects_before_blob_loading(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(size_bytes=snapshot.size_bytes - 1)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {"snapshot": snapshot.pk})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(
            any(q["sql"].startswith('SELECT "ingestion_datasnapshot"."payload"') for q in queries)
        )

    def test_output_escapes_provider_strings_and_exports_whole_copy(self):
        self.copy([{**self.row[0], "facility_name": '<script>alert("test")</script>'}])
        response = self.client.get(self.url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("test")</script>')
        self.assertContains(response, "Pobierz całą kopię JSON")
        self.assertContains(response, "nie tylko filtrowane wyniki")
        detail = self.client.get(f"/data/{self.channel.pk}/")
        self.assertContains(detail, self.url)
