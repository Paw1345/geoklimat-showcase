import copy
import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import Mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ingestion.data_service import collect, store_snapshot
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease
from ingestion.pig_data import ATTRIBUTION, channel_url
from ingestion.pig_data import config as pig_config
from ingestion.transport import FetchError, Response


def feature(identity=1):
    return {
        "type": "Feature",
        "id": identity,
        "properties": {"OBJECTID": identity, "RAW_DATE": "old"},
        "geometry": {"type": "Polygon", "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]]},
    }


class PigDataTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="a" * 64, sheets={})
        self.registry = RegistryEntry.objects.create(
            release=release,
            registry_id=65,
            name="MIDAS",
            organization="PIG-PIB",
            priority="P1",
            master={},
            technical={},
            integration_state="needs_connector",
        )
        self.channel = DataChannel.objects.create(
            registry=self.registry,
            slug="pig-midas-0-sample",
            kind="geojson",
            url=channel_url(0),
            region="PL",
            config=pig_config(0),
            attribution=ATTRIBUTION,
        )
        self.data = {
            "type": "FeatureCollection",
            "features": [feature()],
            "exceededTransferLimit": True,
        }

    def http(self, data=None, counts=(200, 200)):
        client = Mock()
        rows = iter(
            [{"count": counts[0]}, self.data if data is None else data, {"count": counts[1]}]
        )
        client.get.side_effect = lambda url: Response(200, json.dumps(next(rows)).encode(), {}, url)
        return client

    def test_geometry_attributes_scope_and_no_invented_observation_time(self):
        http = self.http()
        payload, media, summary = collect(self.channel, http)
        data = json.loads(payload)
        self.assertEqual(data["features"], self.data["features"])
        self.assertEqual(
            (media, summary["records"], summary["source_count"]), ("application/json", 1, 200)
        )
        self.assertTrue(summary["partial"])
        self.assertEqual(summary["retention_basis"], "local_copy_age")
        self.assertIsNone(data["geoklimat_provenance"]["source_generated_at"])
        self.assertEqual(http.get.call_count, 3)
        self.assertIn("resultRecordCount=100", http.get.call_args_list[1].args[0])

    def test_duplicate_ids_invalid_geometry_error_count_and_size_fail_closed(self):
        bad = []
        d = copy.deepcopy(self.data)
        d["features"].append(feature())
        bad.append(d)
        d = copy.deepcopy(self.data)
        d["features"][0]["geometry"] = None
        bad.append(d)
        d = copy.deepcopy(self.data)
        d["features"][0]["geometry"]["coordinates"][0][1] = [200, 50]
        bad.append(d)
        d = copy.deepcopy(self.data)
        d["features"][0]["properties"]["OBJECTID"] = True
        bad.append(d)
        d = copy.deepcopy(self.data)
        d["features"] = [feature(i) for i in range(101)]
        bad.append(d)
        bad.extend([{"error": {"code": 404}}, {"type": "FeatureCollection", "features": []}])
        for data in bad:
            with self.subTest(data=str(data)[:30]), self.assertRaises(FetchError):
                collect(self.channel, self.http(data))
        with self.assertRaises(FetchError):
            collect(self.channel, self.http(counts=(200, 201)))
        with override_settings(DATA_AUTO_MAX_BYTES=1), self.assertRaises(FetchError):
            collect(self.channel, self.http())

    def test_profile_and_custom_url_guards(self):
        for kwargs in ({"request_url": channel_url(0)}, {"on_demand": True}):
            with self.assertRaises(FetchError):
                collect(self.channel, self.http(), **kwargs)
        self.channel.config["feature_limit"] = 1000
        with self.assertRaises(FetchError):
            collect(self.channel, self.http())

    def test_snapshot_repeat_expiry_and_export_provenance_hash_read_only(self):
        payload, media, summary = collect(self.channel, self.http())
        snapshot, created = store_snapshot(self.channel, payload, media, summary)
        old_expiry = snapshot.expires_at
        repeat, created = store_snapshot(self.channel, payload, media, summary)
        self.assertFalse(created)
        self.assertEqual(snapshot.pk, repeat.pk)
        self.assertEqual(repeat.expires_at, old_expiry)
        response = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertEqual(response.status_code, 200)
        provenance = json.loads(response.content)["geoklimat_provenance"]
        self.assertEqual(provenance["copy_acquired_at"], snapshot.fetched_at.isoformat())
        self.assertEqual(response["ETag"], '"' + hashlib.sha256(response.content).hexdigest() + '"')
        snapshot.refresh_from_db()
        self.assertEqual(bytes(snapshot.payload), payload)
        snapshot.fetched_at = timezone.now() - timedelta(days=31)
        snapshot.save()
        self.assertEqual(
            self.client.get(reverse("data_download", args=[snapshot.pk])).status_code, 404
        )

    def test_configure_dry_apply_idempotent_admin_preserved(self):
        self.channel.delete()
        call_command("configure_pig_data", stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 0)
        call_command("configure_pig_data", apply=True, stdout=io.StringIO())
        call_command("configure_pig_data", apply=True, stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 3)
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())
        edited = DataChannel.objects.get(slug="pig-midas-1-sample")
        edited.config = {"admin": True}
        edited.save()
        with self.assertRaises(CommandError):
            call_command("configure_pig_data", apply=True, stdout=io.StringIO())
        self.registry.refresh_from_db()
        self.assertEqual(self.registry.integration_state, "needs_connector")
