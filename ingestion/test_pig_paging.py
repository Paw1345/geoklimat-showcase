import io
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ingestion.data_paging import advance_harvest
from ingestion.data_service import collect
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.pig_data import channel_url
from ingestion.pig_paging import config, slug
from ingestion.transport import FetchError, Response


class PigPagingTests(TestCase):
    def setUp(self):
        self.page_patch = patch("ingestion.pig_paging.PAGE_SIZE", 2)
        self.page_patch.start()
        self.addCleanup(self.page_patch.stop)
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="f" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            release=release,
            registry_id=65,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=slug(0),
            kind="geojson",
            region="PL",
            url=channel_url(0),
            config=config(0),
        )
        self.ids = [1, 3, 5, 7, 9]

    def http(self):
        http = Mock()

        def get(url):
            params = parse_qs(urlsplit(url).query)
            if params.get("returnIdsOnly") == ["true"]:
                data = {"objectIdFieldName": "OBJECTID", "objectIds": self.ids}
            elif params.get("returnCountOnly") == ["true"]:
                data = {"count": len(self.ids)}
            else:
                ids = list(map(int, params["objectIds"][0].split(",")))
                data = {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": i,
                            "properties": {"OBJECTID": i},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                            },
                        }
                        for i in ids
                    ],
                }
            return Response(200, json.dumps(data).encode(), {}, url)

        http.get.side_effect = get
        return http

    def test_resume_exact_ids_and_complete_unique_coverage(self):
        first = advance_harvest(self.channel, self.http())
        self.assertEqual((first["status"], first["pages"], first["records"]), ("collecting", 2, 4))
        last = advance_harvest(self.channel, self.http())
        self.assertEqual((last["status"], last["pages"], last["records"]), ("complete", 3, 5))
        self.assertEqual(DataSnapshot.objects.count(), 3)
        got = [
            f["id"]
            for s in DataSnapshot.objects.order_by("pk")
            for f in json.loads(bytes(s.payload))["features"]
        ]
        self.assertEqual(got, self.ids)

    def test_failure_preserves_committed_cursor_and_resumes(self):
        http = self.http()
        get = http.get.side_effect

        def interrupted(url):
            if "objectIds=5%2C7" in url:
                raise FetchError("offline")
            return get(url)

        http.get.side_effect = interrupted
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, http)
        h = DataHarvest.objects.get()
        self.assertEqual((h.status, h.pages, h.records), ("failed", 1, 2))
        result = advance_harvest(self.channel, self.http())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(DataSnapshot.objects.count(), 3)

    def test_equal_count_identity_change_does_not_reset_or_advance(self):
        advance_harvest(self.channel, self.http())
        before = DataHarvest.objects.get().next_url
        self.ids = [1, 3, 5, 7, 11]
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http())
        h = DataHarvest.objects.get()
        self.assertEqual(h.next_url, before)
        self.assertEqual(h.records, 4)
        self.assertEqual(DataSnapshot.objects.count(), 2)

    def test_repeated_or_missing_page_rejected_before_commit(self):
        http = self.http()
        get = http.get.side_effect

        def wrong(url):
            r = get(url)
            if "objectIds=" in url:
                d = json.loads(r.body)
                d["features"][0]["id"] = 99
                r.body = json.dumps(d).encode()
            return r

        http.get.side_effect = wrong
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, http)
        self.assertEqual(DataHarvest.objects.get().records, 0)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_quota_and_cursor_commit_failure_are_atomic(self):
        with override_settings(DATA_STORAGE_MAX_BYTES=1), self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http())
        self.assertFalse(DataSnapshot.objects.exists())
        self.assertEqual(DataHarvest.objects.get().records, 0)
        original = DataHarvest.save

        def fail(obj, *args, **kwargs):
            if obj.records > 0:
                raise RuntimeError("commit failed")
            return original(obj, *args, **kwargs)

        with patch.object(DataHarvest, "save", fail), self.assertRaises(RuntimeError):
            advance_harvest(self.channel, self.http())
        self.assertFalse(DataSnapshot.objects.exists())
        self.assertEqual(DataHarvest.objects.get().records, 0)

    def test_probe_and_configuration_do_not_mutate_existing_samples(self):
        payload, media, summary = collect(self.channel, self.http())
        self.assertEqual(summary["source_count"], 5)
        self.assertFalse(DataHarvest.objects.exists())
        self.assertFalse(DataSnapshot.objects.exists())
        self.channel.delete()
        sample = DataChannel.objects.create(
            registry=RegistryEntry.objects.get(),
            slug="pig-midas-0-sample",
            kind="geojson",
            config={"admin": True},
            enabled=True,
        )
        call_command("configure_pig_scans", stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 1)
        call_command("configure_pig_scans", apply=True, stdout=io.StringIO())
        call_command("configure_pig_scans", apply=True, stdout=io.StringIO())
        self.assertEqual(DataChannel.objects.count(), 4)
        sample.refresh_from_db()
        self.assertEqual(sample.config, {"admin": True})
        self.assertTrue(sample.enabled)
        ch = DataChannel.objects.get(slug=slug(1))
        ch.config = {"admin": True}
        ch.save()
        with self.assertRaises(CommandError):
            call_command("configure_pig_scans", apply=True, stdout=io.StringIO())
