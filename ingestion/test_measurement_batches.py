import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from ingestion.measurement_history import query
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError


class MeasurementBatchTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="c" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=34,
            release=release,
            master={},
            technical={},
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug="imgw-synop",
            name="IMGW",
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/synop",
            access_approved=True,
        )
        self.now = datetime(2026, 10, 2, 12, tzinfo=UTC)
        self.staff = get_user_model().objects.create_user(username="operator", is_staff=True)
        self.snapshots = [self.snapshot(i) for i in range(3)]

    def snapshot(self, age, station="123"):
        payload = json.dumps(
            [
                {
                    "id_stacji": station,
                    "stacja": "Test",
                    "data_pomiaru": "2026-10-02",
                    "godzina_pomiaru": "8",
                    "temperatura": str(age),
                }
            ]
        ).encode()
        return DataSnapshot.objects.create(
            channel=self.channel,
            payload=payload,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type="application/json",
            fetched_at=self.now - timedelta(hours=age),
            expires_at=self.now + timedelta(days=29),
            summary={"kind": "imgw", "retention_version": 1, "observation_timezone": "UTC"},
        )

    def run_query(self, station="123", **kwargs):
        with patch("ingestion.measurement_history.timezone.now", return_value=self.now):
            return query(self.channel.pk, station, **kwargs)

    def test_small_batches_cover_each_copy_once_keep_budget_and_no_http(self):
        with (
            patch("ingestion.measurement_batches.COPIES_PER_BATCH", 1),
            patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")),
        ):
            first = self.run_query(copy_page=1)[0]
            token = first["copy_batches"]["token"]
            results = [first, *[self.run_query(copy_page=p, copy_token=token)[0] for p in [2, 3]]]
        ids = [r["receipts"][0]["snapshot_id"] for r in results]
        self.assertEqual(ids, [s.pk for s in self.snapshots])
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(first["copy_batches"]["total_copies"], 3)
        self.assertFalse(first["coverage"]["all_available_copies_selected"])
        self.assertTrue(first["coverage"]["selected_copies_payload_verified"])
        self.assertEqual(DataSnapshot.objects.count(), 3)

    def test_byte_budget_splits_instead_of_bypassing_full_refusal(self):
        budget = self.snapshots[0].size_bytes + 10
        with override_settings(DATA_MANUAL_MAX_BYTES=budget):
            with self.assertRaisesMessage(FetchError, "read budget"):
                self.run_query()
            first = self.run_query(copy_page=1)[0]
            self.assertEqual(first["copy_batches"]["pages"], 3)
            self.assertLessEqual(first["coverage"]["read_bytes"], budget)
        with override_settings(DATA_MANUAL_MAX_BYTES=10), self.assertRaises(FetchError):
            self.run_query(copy_page=1)

    def test_new_copies_do_not_shift_existing_scope(self):
        with patch("ingestion.measurement_batches.COPIES_PER_BATCH", 1):
            first = self.run_query(copy_page=1)[0]
            self.now += timedelta(hours=1)
            self.snapshot(0)
            second = self.run_query(copy_page=2, copy_token=first["copy_batches"]["token"])[0]
        self.assertEqual(second["copy_batches"]["total_copies"], 3)
        self.assertEqual(second["receipts"][0]["snapshot_id"], self.snapshots[1].pk)
        self.assertEqual(second["copy_batches"]["anchor"], first["copy_batches"]["anchor"])

    def test_expiry_or_changed_metadata_stops_scope_not_silent_skip(self):
        first = self.run_query(copy_page=1)[0]
        token = first["copy_batches"]["token"]
        self.snapshots[2].expires_at = self.now
        self.snapshots[2].save()
        with self.assertRaisesMessage(FetchError, "scope changed"):
            self.run_query(copy_page=1, copy_token=token)

    def test_payload_changed_even_with_same_metadata_refuses_selected_batch(self):
        first = self.run_query(copy_page=1)[0]
        row = self.snapshots[1]
        row.payload = bytes(row.payload).replace(b"Test", b"Fail")
        row.save()
        with self.assertRaises(FetchError):
            self.run_query(copy_page=1, copy_token=first["copy_batches"]["token"])

    def test_outside_batch_corruption_not_misrepresented_as_verified(self):
        row = self.snapshots[2]
        row.sha256 = "0" * 64
        row.save()
        with patch("ingestion.measurement_batches.COPIES_PER_BATCH", 1):
            first = self.run_query(copy_page=1)[0]
            self.assertFalse(first["coverage"]["all_available_copies_selected"])
            with self.assertRaises(FetchError):
                self.run_query(copy_page=3, copy_token=first["copy_batches"]["token"])

    def test_tampered_cross_channel_expired_and_changed_days_tokens_refused(self):
        first = self.run_query(copy_page=1)[0]
        token = first["copy_batches"]["token"]
        with self.assertRaises(FetchError):
            self.run_query(copy_page=1, copy_token=token + "x")
        with self.assertRaises(FetchError):
            self.run_query(copy_page=1, copy_token=token, days=7)
        with self.assertRaises(FetchError):
            self.run_query(copy_token=token)
        with patch("django.core.signing.time.time", return_value=time.time() + 3601):
            with self.assertRaises(FetchError):
                self.run_query(copy_page=1, copy_token=token)
        other = DataChannel.objects.create(
            registry=self.channel.registry,
            slug="other",
            kind="imgw",
            region="PL",
            url=self.channel.url,
            access_approved=True,
        )
        with self.assertRaises(FetchError):
            query(other.pk, copy_page=1, copy_token=token)

    def test_invalid_page_bounds_and_budget_change_refused(self):
        for value in [True, 0, -1, 2001, "1", 2]:
            with self.assertRaises(FetchError):
                self.run_query(copy_page=value)
        first = self.run_query(copy_page=1)[0]
        with override_settings(DATA_MANUAL_MAX_BYTES=1000), self.assertRaises(FetchError):
            self.run_query(copy_page=1, copy_token=first["copy_batches"]["token"])

    def test_station_absent_in_batch_returns_empty_all_mode_still_refuses(self):
        first = self.run_query("999", copy_page=1)[0]
        self.assertFalse(first["station_found_in_selected_copies"])
        self.assertEqual(first["records"], [])
        with self.assertRaises(FetchError):
            self.run_query("999")

    def test_global_metadata_timezone_guard_across_batches(self):
        row = self.snapshots[2]
        row.summary = {**row.summary, "observation_timezone": "Europe/Warsaw"}
        row.save()
        with (
            patch("ingestion.measurement_batches.COPIES_PER_BATCH", 1),
            self.assertRaises(FetchError),
        ):
            self.run_query(copy_page=1)

    def test_full_mode_unchanged_and_single_batch_not_marked_partial(self):
        all_result = self.run_query()[0]
        batch_result = self.run_query(copy_page=1)[0]
        self.assertEqual(all_result["records"], batch_result["records"])
        self.assertTrue(batch_result["coverage"]["all_available_copies_selected"])
        self.assertEqual(all_result["copy_batches"]["mode"], "all")

    def test_ui_links_exports_keep_scope_and_error_can_restart(self):
        self.client.force_login(self.staff)
        params = {
            "channel": self.channel.pk,
            "station": "123",
            "days": 30,
            "limit": 10,
            "format": "html",
            "copy_page": 1,
        }
        with (
            patch("ingestion.measurement_batches.COPIES_PER_BATCH", 1),
            patch("ingestion.measurement_history.timezone.now", return_value=self.now),
        ):
            response = self.client.get("/data/measurements/", params)
            self.assertContains(response, "Wynik częściowy")
            links = response.context["links"]
            next_params = parse_qs(urlsplit(links["next"]).query)
            next_response = self.client.get(
                "/data/measurements/", {k: v[0] for k, v in next_params.items()}
            )
            self.assertContains(next_response, "Paczka kopii 2 z 3")
            self.assertEqual(
                next_response.context["result"]["copy_batches"]["token"],
                response.context["result"]["copy_batches"]["token"],
            )
            for key in ["json", "csv"]:
                response = self.client.get("/data/measurements/" + links[key])
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertEqual(
                    response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"'
                )
                if key == "json":
                    self.assertFalse(response.json()["coverage"]["all_available_copies_selected"])
                else:
                    self.assertIn(b"copy_batch_page", response.content)
            response = self.client.get("/data/measurements/", {**params, "copy_token": "bad"})
            self.assertEqual(response.status_code, 409)
            self.assertContains(response, "Rozpocznij nowy przegląd", status_code=409)
        self.assertEqual(
            self.client.get("/data/measurements/", {**params, "copy_page": 0}).status_code, 400
        )
