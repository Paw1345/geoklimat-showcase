import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion.data_service import collect, run_data, store_snapshot
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response


class DataTests(TestCase):
    def test_busy_audit_preserves_approval(self):
        from django.core.management.base import CommandError

        self.channel.access_approved = self.channel.enabled = True
        self.channel.save()
        with (
            patch(
                "ingestion.management.commands.audit_data.run_data",
                return_value=[{"status": "busy"}],
            ),
            self.assertRaises(CommandError),
        ):
            call_command("audit_data", source=self.channel.slug, enable=True, stdout=io.StringIO())
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.enabled)
        self.assertTrue(self.channel.access_approved)

    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=34,
            release=release,
            organization="IMGW",
            name="Meteo",
            country="Poland",
            level="National",
            priority="P1",
            technical_status="TEST",
            master={},
            technical={},
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="test",
            name="Test",
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/meteo",
            attribution="IMGW",
        )

    def client_for(self, value):
        client = Mock()
        client.get.return_value = Response(200, json.dumps(value).encode(), {}, self.channel.url)
        return client

    def test_imgw_time_filter_and_stale_fields(self):
        now = timezone.now()
        current = now.isoformat()
        old = (now - timedelta(days=31)).isoformat()
        rows = [
            {
                "temperatura_powietrza": "12",
                "temperatura_powietrza_data": current,
                "opad_10min": "1",
                "opad_10min_data": old,
            },
            {"temperatura_powietrza": "1", "temperatura_powietrza_data": old},
        ]
        payload, media, summary = collect(self.channel, self.client_for(rows))
        parsed = json.loads(payload)
        self.assertEqual(media, "application/json")
        self.assertEqual(len(parsed), 1)
        self.assertIsNone(parsed[0]["opad_10min"])
        self.assertEqual(summary["records"], 1)
        with self.assertRaises(FetchError):
            collect(self.channel, self.client_for([rows[1]]))

    def test_synop_date_and_hour(self):
        now = timezone.now() - timedelta(hours=1)
        payload, _, summary = collect(
            self.channel,
            self.client_for(
                [
                    {
                        "data_pomiaru": now.strftime("%Y-%m-%d"),
                        "godzina_pomiaru": str(now.hour),
                        "temperatura": "15",
                    }
                ]
            ),
        )
        self.assertIn(b"15", payload)
        self.assertEqual(summary["records"], 1)

    def test_gios_null_is_not_zero(self):
        self.channel.kind = "gios"
        rows = {
            "Lista danych pomiarowych": [
                {"Data": timezone.now().isoformat(), "Wartość": None},
                {"Data": "2001-01-01T00:00:00Z", "Wartość": 2},
            ]
        }
        payload, _, summary = collect(self.channel, self.client_for(rows))
        self.assertEqual(summary["records"], 1)
        self.assertIsNone(json.loads(payload)["Lista danych pomiarowych"][0]["Wartość"])

    def test_quota_dedup_and_observation_expiry(self):
        summary = {"oldest_observation": (timezone.now() - timedelta(days=10)).isoformat()}
        snapshot, created = store_snapshot(self.channel, b"abc", "application/json", summary)
        self.assertTrue(created)
        self.assertLess(snapshot.expires_at, timezone.now() + timedelta(days=21))
        _, created = store_snapshot(self.channel, b"abc", "application/json", summary)
        self.assertFalse(created)
        with override_settings(DATA_STORAGE_MAX_BYTES=4), self.assertRaises(FetchError):
            store_snapshot(self.channel, b"def", "application/json", {})
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_prune_is_scoped_and_dry_by_default(self):
        snapshot, _ = store_snapshot(self.channel, b"abc", "application/json", {})
        snapshot.expires_at = timezone.now() - timedelta(days=1)
        snapshot.save()
        call_command("prune_data", stdout=io.StringIO())
        self.assertTrue(DataSnapshot.objects.exists())
        call_command("prune_data", apply=True, stdout=io.StringIO())
        self.assertFalse(DataSnapshot.objects.exists())
        self.assertTrue(RegistryEntry.objects.exists())
        self.assertTrue(DataChannel.objects.exists())

    def test_scheduler_approval_and_files_gate(self):
        self.channel.enabled = True
        self.channel.save()
        with patch("ingestion.data_service.Client") as client:
            self.assertEqual(run_data(force=True), [])
            client.return_value.get.assert_not_called()
        self.channel.kind = "file"
        with self.assertRaises(FetchError):
            collect(self.channel, Mock())
        self.channel.region = "GLOBAL"
        with self.assertRaises(FetchError):
            collect(self.channel, Mock(), on_demand=True)

    def test_failed_fetch_has_no_snapshot_and_records_error(self):
        self.channel.enabled = self.channel.access_approved = True
        self.channel.save()
        with patch("ingestion.data_service.Client") as client:
            client.return_value.get.side_effect = FetchError("blocked")
            result = run_data(force=True)
        self.assertEqual(result[0]["status"], "failed")
        self.assertFalse(DataSnapshot.objects.exists())
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.last_error, "blocked")

    def test_stac_bounded_and_partial_assets_not_fetched(self):
        self.channel.kind = "stac"
        self.channel.config = {"bbox": [14, 49, 24, 55], "collection": "sentinel-2-l2a"}
        response = {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {"datetime": timezone.now().isoformat()},
                    "assets": {"image": {"href": "https://unapproved.example/image.tif"}},
                }
            ],
            "links": [{"rel": "next", "href": "https://unapproved.example/next"}],
        }
        client = self.client_for(response)
        _, _, summary = collect(self.channel, client)
        self.assertTrue(summary["partial"])
        self.assertFalse(summary["assets_downloaded"])
        self.assertEqual(client.get.call_count, 1)
        self.assertIn("collections=sentinel-2-l2a", client.get.call_args.args[0])

    def test_geojson_partial_and_payload_download(self):
        self.channel.kind = "geojson"
        data = {"type": "FeatureCollection", "features": [{"geometry": None}], "numberMatched": 99}
        payload, media, summary = collect(self.channel, self.client_for(data))
        self.assertTrue(summary["partial"])
        snapshot, _ = store_snapshot(self.channel, payload, media, summary)
        self.assertContains(self.client.get("/data/"), "30 dni")
        self.assertEqual(self.client.get(f"/data/{self.channel.pk}/").status_code, 200)
        response = self.client.get(f"/data/snapshots/{snapshot.pk}/download/")
        self.assertEqual(response.content, payload)
        self.assertIn("attachment", response["Content-Disposition"])
        snapshot.expires_at = timezone.now() - timedelta(seconds=1)
        snapshot.save()
        self.assertEqual(
            self.client.get(f"/data/snapshots/{snapshot.pk}/download/").status_code, 404
        )

    def test_on_demand_requires_staff_post_approval_and_csrf(self):
        from django.test import Client

        self.channel.kind = "file"
        self.channel.access_approved = True
        self.channel.save()
        url = f"/data/{self.channel.pk}/request/"
        self.assertEqual(self.client.post(url).status_code, 302)
        user = get_user_model().objects.create_user("staff", is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        self.assertEqual(csrf_client.post(url).status_code, 403)
        with patch("core.data_views.enqueue", return_value=(None, True)):
            self.assertEqual(self.client.post(url).status_code, 302)
