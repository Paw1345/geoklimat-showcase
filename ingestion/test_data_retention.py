import hashlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings

from ingestion.data_retention import prune_cache, retained_snapshot
from ingestion.data_service import collect, store_snapshot
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.observations import filter_observations, timestamp
from ingestion.transport import Response


class ObservationRetentionTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="c" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=34, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="retention",
            kind="imgw",
            region="PL",
            url="https://example.org",
            config={"timezone": "UTC"},
        )
        self.now = datetime(2026, 9, 23, 12, tzinfo=UTC)
        self.rows = [
            {
                "id": "mixed",
                "temperature": 12,
                "temperature_data": self.now.isoformat(),
                "rain": 4,
                "rain_data": (self.now - timedelta(days=29)).isoformat(),
            },
            {
                "id": "older",
                "temperature": 5,
                "temperature_data": (self.now - timedelta(days=29)).isoformat(),
            },
        ]

    def snapshot(self):
        client = Mock()
        client.get.return_value = Response(
            200, json.dumps(self.rows).encode(), {}, self.channel.url
        )
        with patch("django.utils.timezone.now", return_value=self.now):
            payload, media, summary = collect(self.channel, client)
            snapshot = store_snapshot(self.channel, payload, media, summary)[0]
        # Model field's callable default was bound before the clock patch.
        snapshot.fetched_at = self.now
        snapshot.save(update_fields=["fetched_at"])
        return snapshot

    def test_new_page_deadline_keeps_newer_neighbour(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot.expires_at, self.now + timedelta(days=30))
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=2)):
            before = bytes(snapshot.payload)
            dry = prune_cache()
            snapshot.refresh_from_db()
            self.assertEqual(bytes(snapshot.payload), before)
            self.assertEqual(dry["trimmed"], 1)
            result = prune_cache(apply=True)
            self.assertEqual(result, dry)
            snapshot.refresh_from_db()
            rows = json.loads(bytes(snapshot.payload))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["temperature"], 12)
            self.assertIsNone(rows[0]["rain"])
            self.assertEqual(snapshot.summary["records"], 1)
            self.assertEqual(snapshot.size_bytes, len(bytes(snapshot.payload)))
            self.assertEqual(snapshot.sha256, hashlib.sha256(bytes(snapshot.payload)).hexdigest())
            self.assertEqual(snapshot.expires_at, self.now + timedelta(days=30))
            self.assertEqual(prune_cache(apply=True)["trimmed"], 0)

    def test_export_and_preview_filter_even_without_worker(self):
        snapshot = self.snapshot()
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=2)):
            response = self.client.get(f"/data/snapshots/{snapshot.pk}/download/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(json.loads(response.content)), 1)
            self.assertIsNone(json.loads(response.content)[0]["rain"])
            self.assertEqual(
                response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"'
            )
            preview = self.client.get(f"/data/{self.channel.pk}/")
            self.assertNotContains(preview, "older")
            snapshot.refresh_from_db()
            self.assertEqual(len(json.loads(bytes(snapshot.payload))), 2)  # GET has no writes.

    def test_legacy_expiry_upgraded_without_resetting_age(self):
        snapshot = self.snapshot()
        snapshot.summary.pop("retention_version")
        snapshot.summary.pop("newest_observation")
        snapshot.expires_at = self.now + timedelta(days=1)
        snapshot.save()
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=2)):
            prune_cache(apply=True)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.expires_at, self.now + timedelta(days=30))
        self.assertEqual(snapshot.fetched_at, self.now)
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=30)):
            self.assertEqual(
                self.client.get(f"/data/snapshots/{snapshot.pk}/download/").status_code, 404
            )
            prune_cache(apply=True)
        self.assertFalse(DataSnapshot.objects.exists())
        self.assertTrue(RegistryEntry.objects.exists())

    def test_exact_cutoff_future_invalid_and_missing_dates_fail_closed(self):
        values = [self.now - timedelta(days=30), self.now + timedelta(minutes=6)]
        rows = [{"temperature": 1, "temperature_data": stamp.isoformat()} for stamp in values]
        rows.extend(
            [
                {"temperature": 1, "temperature_data": None},
                {"temperature": 1, "temperature_data": "2026-02-31"},
            ]
        )
        filtered, stamps = filter_observations(rows, "imgw", self.now)
        self.assertEqual((filtered, stamps), ([], []))
        self.assertIsNone(timestamp("2026-02-31"))

    def test_gios_and_stac_trim_individual_rows(self):
        old = (self.now - timedelta(days=31)).isoformat()
        current = self.now.isoformat()
        gios = {
            "Lista danych pomiarowych": [
                {"Data": old, "Wartość": 9},
                {"Data": current, "Wartość": None},
            ]
        }
        filtered, stamps = filter_observations(gios, "gios", self.now)
        self.assertEqual(len(stamps), 1)
        self.assertIsNone(filtered["Lista danych pomiarowych"][0]["Wartość"])
        stac = {
            "features": [{"properties": {"datetime": stamp}} for stamp in [old, current]],
            "numberMatched": 100,
        }
        filtered, stamps = filter_observations(stac, "stac", self.now)
        self.assertEqual(len(filtered["features"]), 1)
        self.assertEqual(filtered["numberMatched"], 100)
        self.assertEqual(len(stac["features"]), 2)

    def test_snapshot_pins_timezone_across_profile_edit(self):
        snapshot = self.snapshot()
        self.channel.config["timezone"] = "Europe/Warsaw"
        self.channel.save()
        snapshot.channel = self.channel
        _, summary, _ = retained_snapshot(snapshot, self.now + timedelta(hours=1))
        self.assertEqual(summary["observation_timezone"], "UTC")

    def test_malformed_cached_payload_never_exported_or_extended(self):
        snapshot = self.snapshot()
        snapshot.payload = b"not json"
        snapshot.save()
        with patch("django.utils.timezone.now", return_value=self.now + timedelta(days=2)):
            self.assertEqual(
                self.client.get(f"/data/snapshots/{snapshot.pk}/download/").status_code, 404
            )
            self.assertEqual(prune_cache(apply=True)["invalid"], 1)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.expires_at, self.now + timedelta(days=30))

    @override_settings(DATA_RETENTION_DAYS=60)
    def test_config_cannot_expand_authorized_window(self):
        self.assertEqual(self.snapshot().expires_at, self.now + timedelta(days=30))
