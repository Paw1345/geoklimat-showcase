import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from core.measurements import csv_payload, safe_cell
from ingestion.measurement_history import query
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError


class MeasurementHistoryTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release, registry_id=34, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="imgw-synop",
            name="IMGW SYNOP",
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/synop",
            access_approved=True,
            attribution="IMGW source attribution",
        )
        self.now = datetime(2026, 10, 2, 12, tzinfo=UTC)
        self.row = {
            "id_stacji": "12295",
            "stacja": "Białystok",
            "data_pomiaru": "2026-10-02",
            "godzina_pomiaru": "8",
            "temperatura": "15.2",
            "cisnienie": None,
        }
        self.first = self.snapshot([self.row], age=2)
        self.second = self.snapshot([self.row], age=1)
        self.staff = get_user_model().objects.create_user(username="operator", is_staff=True)

    def snapshot(self, rows, age=0, zone="UTC", **overrides):
        payload = json.dumps(rows, ensure_ascii=False).encode()
        values = dict(
            channel=self.channel,
            payload=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            media_type="application/json",
            fetched_at=self.now - timedelta(hours=age),
            expires_at=self.now + timedelta(days=29),
            summary={"kind": "imgw", "retention_version": 1, "observation_timezone": zone},
        )
        values.update(overrides)
        return DataSnapshot.objects.create(**values)

    def run_query(self, *args, **kwargs):
        with patch("ingestion.measurement_history.timezone.now", return_value=self.now):
            return query(self.channel.pk, *args, **kwargs)

    def test_inventory_dedup_null_provenance_no_http_or_writes(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            inventory = self.run_query()[0]
            result, payload, digest = self.run_query("12295")
        self.assertEqual(inventory["stations"], [{"id": "12295", "names": ["Białystok"]}])
        self.assertEqual(result["unique_values"], 2)
        self.assertEqual(len(result["records"][0]["snapshot_ids"]), 2)
        self.assertTrue(any(row["value"] is None for row in result["records"]))
        self.assertTrue(all(row["unit"] is None for row in result["records"]))
        self.assertFalse(result["units_verified"])
        self.assertFalse(result["coverage"]["full_history_verified"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
        self.assertEqual(DataSnapshot.objects.count(), 2)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.last_status, "not_tested")

    def test_value_variants_preserved_not_silently_replaced(self):
        self.snapshot([{**self.row, "temperatura": "16.0"}])
        result = self.run_query("12295", "temperatura")[0]
        self.assertEqual(result["variant_groups"], 1)
        self.assertEqual({row["value"] for row in result["records"]}, {"15.2", "16.0"})
        self.assertTrue(all(row["value_variants"] == 2 for row in result["records"]))

    def test_limit_keeps_total_and_coverage_not_just_displayed_rows(self):
        self.snapshot([{**self.row, "data_pomiaru": "2026-10-01"}])
        result = self.run_query("12295", limit=1)[0]
        self.assertTrue(result["truncated"])
        self.assertEqual(result["unique_values"], 4)
        self.assertEqual(result["coverage"]["observed_dates_utc"], ["2026-10-01", "2026-10-02"])
        self.assertIn("2026-09-03", result["coverage"]["dates_without_saved_observations_utc"])

    def test_expired_and_hard_age_copies_not_read(self):
        self.first.expires_at = self.now
        self.first.payload = b"corrupt but expired"
        self.first.save()
        self.second.fetched_at = self.now - timedelta(days=30)
        self.second.save()
        self.assertEqual(self.run_query()[0]["coverage"]["copy_count"], 0)
        with self.assertRaises(FetchError):
            self.run_query("12295")

    def test_corrupt_copy_after_limit_refuses_whole_result(self):
        self.first.sha256 = "b" * 64
        self.first.save()
        with self.assertRaises(FetchError):
            self.run_query("12295", limit=1)

    def test_size_media_and_summary_validation(self):
        for field, value in [("size_bytes", 0), ("media_type", "text/html"), ("summary", {})]:
            original = getattr(self.first, field)
            setattr(self.first, field, value)
            self.first.save()
            with self.assertRaises(FetchError):
                self.run_query("12295")
            setattr(self.first, field, original)
            self.first.save()

    @override_settings(DATA_MANUAL_MAX_BYTES=10)
    def test_read_budget_uses_actual_bytes_not_forged_size(self):
        DataSnapshot.objects.update(size_bytes=0)
        with self.assertRaisesMessage(FetchError, "read budget"):
            self.run_query("12295")

    def test_output_budget(self):
        with patch("ingestion.measurement_history.RESULT_BYTES", 10), self.assertRaises(FetchError):
            self.run_query("12295")

    def test_timezone_pinned_and_mixed_refused(self):
        self.channel.config = {"timezone": "Europe/Warsaw"}
        self.channel.save()
        self.assertEqual(
            self.run_query("12295")[0]["records"][0]["time_utc"], "2026-10-02T08:00:00+00:00"
        )
        self.snapshot([self.row], zone="Europe/Warsaw")
        with self.assertRaises(FetchError):
            self.run_query("12295")

    def test_parameter_specific_times_for_meteo_and_hydro(self):
        DataSnapshot.objects.all().delete()
        for profile, row, field, expected in [
            (
                "meteo",
                {
                    "kod_stacji": "123",
                    "nazwa_stacji": "Test",
                    "temperatura_powietrza": "0",
                    "temperatura_powietrza_data": "2026-10-02 09:00:00",
                    "opad_10min": "7",
                    "opad_10min_data": "2026-08-01 09:00:00",
                },
                "temperatura_powietrza",
                "0",
            ),
            (
                "hydro",
                {
                    "id_stacji": "123",
                    "stacja": "Test",
                    "stan_wody": "200",
                    "stan_wody_data_pomiaru": "2026-10-02 10:00:00",
                    "przeplyw": "5",
                    "przeplyw_data": None,
                },
                "stan_wody",
                "200",
            ),
        ]:
            self.channel.url = "https://danepubliczne.imgw.pl/api/data/" + profile
            self.channel.save()
            self.snapshot([row])
            result = self.run_query("123")[0]
            self.assertEqual(len(result["records"]), 1)
            self.assertEqual(result["records"][0]["parameter"], field)
            self.assertEqual(result["records"][0]["value"], expected)
            self.assertEqual(result["ignored_nonnull_values_without_recent_time"], 1)
            DataSnapshot.objects.all().delete()

    def test_future_old_and_invalid_dates_never_become_observations(self):
        DataSnapshot.objects.all().delete()
        for day, hour in [
            ("2026-08-01", "8"),
            ("2026-10-03", "8"),
            ("2026-10-02", "24"),
            ("2026-02-30", "8"),
        ]:
            self.snapshot([{**self.row, "data_pomiaru": day, "godzina_pomiaru": hour}])
        self.assertEqual(self.run_query()[0]["stations"], [])

    def test_invalid_filters_profile_and_revoked_approval(self):
        for args in [
            ("abc",),
            ("١٢",),
            ("12295", "secret"),
            ("12295", "", 31),
            ("12295", "", True),
            ("12295", "", 30, 1001),
        ]:
            with self.assertRaises(FetchError):
                self.run_query(*args)
        self.channel.url += "/unexpected"
        self.channel.save()
        with self.assertRaises(FetchError):
            self.run_query("12295")
        self.channel.url = "https://danepubliczne.imgw.pl/api/data/synop"
        self.channel.access_approved = False
        self.channel.save()
        with self.assertRaises(FetchError):
            self.run_query("12295")

    def test_duplicate_stations_and_keys_and_nonfinite_values_refused(self):
        for raw in [
            json.dumps([self.row, self.row]).encode(),
            b'[{"id_stacji":"1","id_stacji":"2"}]',
            json.dumps([{**self.row, "temperatura": float("nan")}]).encode(),
            json.dumps([self.row]).replace('"15.2"', "1e400").encode(),
        ]:
            DataSnapshot.objects.all().delete()
            self.snapshot(
                [], payload=raw, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()
            )
            with self.assertRaises(FetchError):
                self.run_query("12295")

    def test_csv_formula_guard_and_receipts(self):
        for value in ["=1+1", "+1", "-1", "@test", "\t=1", "  =1", "\r=1"]:
            self.assertTrue(safe_cell(value).startswith("'"))
        self.assertEqual(safe_cell("0"), "0")
        self.assertEqual(safe_cell(None), "")
        result = self.run_query("12295")[0]
        payload = csv_payload(result)
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertIn(self.first.sha256.encode(), payload)
        self.assertIn(b"raw_value_is_null", payload)
        with patch("core.measurements.RESULT_BYTES", 10), self.assertRaises(FetchError):
            csv_payload(result)

    def test_null_only_day_not_reported_as_available_measurement(self):
        DataSnapshot.objects.all().delete()
        self.snapshot([{**self.row, "temperatura": None}])
        result = self.run_query("12295")[0]
        self.assertEqual(result["null_values"], 2)
        self.assertEqual(result["coverage"]["observed_dates_utc"], [])
        self.assertEqual(result["coverage"]["dates_with_timestamped_null_utc"], ["2026-10-02"])
        self.assertIn("2026-10-02", result["coverage"]["dates_without_saved_observations_utc"])
        self.assertTrue(
            all(
                row["observation_expires_at"] == "2026-11-01T08:00:00+00:00"
                for row in result["records"]
            )
        )

    def test_staff_get_only_views_formats_escaping_and_errors(self):
        path = "/data/measurements/"
        self.assertEqual(self.client.get(path).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(path).status_code, 405)
        self.assertEqual(self.client.get(path).status_code, 200)
        params = {
            "channel": self.channel.pk,
            "station": "12295",
            "days": 30,
            "limit": 1,
            "format": "html",
        }
        with patch("ingestion.measurement_history.timezone.now", return_value=self.now):
            response = self.client.get(path, params)
            self.assertContains(response, "Wynik ograniczony limitem")
            for suffix, mime in [("json", "application/json"), ("csv", "text/csv")]:
                response = self.client.get(path, {**params, "format": suffix})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response["Content-Type"].startswith(mime))
                self.assertEqual(response["X-Content-Type-Options"], "nosniff")
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(
                    response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"'
                )
            response = self.client.get(path, {**params, "station": "999", "format": "json"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(set(response.json()), {"error"})
        self.assertEqual(self.client.get(path, {**params, "days": 31}).status_code, 400)
        self.assertEqual(
            self.client.get(path, {**params, "station": "", "format": "csv"}).status_code, 400
        )
        self.staff.is_staff = False
        self.staff.save()
        self.assertEqual(self.client.get(path).status_code, 302)

    def test_html_provider_text_escaped(self):
        DataSnapshot.objects.all().delete()
        self.snapshot([{**self.row, "stacja": "<script>alert(1)</script>"}])
        self.client.force_login(self.staff)
        with patch("ingestion.measurement_history.timezone.now", return_value=self.now):
            response = self.client.get(
                "/data/measurements/",
                {
                    "channel": self.channel.pk,
                    "days": 30,
                    "limit": 10,
                    "format": "html",
                },
            )
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
