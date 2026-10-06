import io
import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.data_views import export_payload
from ingestion.data_paging import advance_harvest
from ingestion.data_service import collect
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.pig_spatial import PROFILES, channel_url, config, slug
from ingestion.transport import FetchError, Response


class PigSpatialTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="e" * 64, sheets={})
        for rid in [63, 64]:
            RegistryEntry.objects.create(
                release=release,
                registry_id=rid,
                master={},
                technical={},
                integration_state="needs_connector",
            )
        self.ids = list(range(1, 26))
        self.channel = DataChannel.objects.create(
            registry=RegistryEntry.objects.get(registry_id=63),
            slug=slug("jcwpd"),
            kind="geojson",
            region="PL",
            url=channel_url("jcwpd"),
            config=config("jcwpd"),
        )

    def http(self, key="jcwpd"):
        http = Mock()

        def get(url):
            q = parse_qs(urlsplit(url).query)
            if urlsplit(url).path.endswith("/query"):
                if q.get("returnIdsOnly") == ["true"]:
                    data = {"objectIdFieldName": "OBJECTID", "objectIds": self.ids}
                elif q.get("returnCountOnly") == ["true"]:
                    data = {"count": len(self.ids)}
                else:
                    ids = list(map(int, q["objectIds"][0].split(",")))
                    data = {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "id": i,
                                "properties": {"OBJECTID": i, "ST_CH_2019": "history"},
                                "geometry": {
                                    "type": "Polygon",
                                    "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                                },
                            }
                            for i in ids
                        ],
                    }
            else:
                data = {
                    "name": PROFILES[key]["title"],
                    "geometryType": "esriGeometryPolygon",
                    "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}],
                }
            return Response(200, json.dumps(data).encode(), {}, url)

        http.get.side_effect = get
        return http

    def test_resume_complete_and_export_retains_historical_scope(self):
        first = advance_harvest(self.channel, self.http())
        self.assertEqual((first["records"], first["pages"]), (20, 2))
        last = advance_harvest(self.channel, self.http())
        self.assertEqual((last["records"], last["status"]), (25, "complete"))
        s = DataSnapshot.objects.order_by("pk").first()
        data = json.loads(export_payload(s))
        p = data["geoklimat_provenance"]
        self.assertIn("2022–2027", p["dataset"])
        self.assertIn("2019", p["scope_note"])
        self.assertTrue(p["partial"])
        self.assertIsNone(p["source_generated_at"])
        self.assertIn("copy_acquired_at", p)
        self.assertNotIn("copy_acquired_at", json.loads(bytes(s.payload))["geoklimat_provenance"])
        self.assertEqual(data["features"][0]["properties"]["ST_CH_2019"], "history")
        response = self.client.get("/data/" + str(self.channel.pk) + "/")
        self.assertContains(response, "Wznawialny przebieg PIG")

    def test_all_profiles_probe_without_writes(self):
        for key, p in PROFILES.items():
            c = DataChannel(kind="geojson", region="PL", url=channel_url(key), config=config(key))
            payload, media, summary = collect(c, self.http(key))
            self.assertEqual(summary["records"], min(p["limit"], len(self.ids)))
            self.assertEqual(summary["collector"], "pig_spatial_scan")
            self.assertEqual(json.loads(payload)["geoklimat_provenance"]["dataset"], p["dataset"])
        self.assertFalse(DataHarvest.objects.exists())
        self.assertFalse(DataSnapshot.objects.exists())

    def test_future_layer_and_arbitrary_url_refused_without_http(self):
        for field, value in [
            ("url", channel_url("jcwpd").replace("/2/query", "/5/query")),
            ("config", {**config("jcwpd"), "layer": 5}),
        ]:
            old = getattr(self.channel, field)
            setattr(self.channel, field, value)
            http = self.http()
            with self.assertRaises(FetchError):
                collect(self.channel, http)
            http.get.assert_not_called()
            setattr(self.channel, field, old)

    def test_remote_schema_change_preserves_committed_cursor(self):
        advance_harvest(self.channel, self.http())
        before = DataHarvest.objects.get().next_url
        http = self.http()
        get = http.get.side_effect

        def changed(url):
            r = get(url)
            if not urlsplit(url).path.endswith("/query"):
                d = json.loads(r.body)
                d["name"] = "JCWPd - cykl planistyczny 2028-2033"
                r.body = json.dumps(d).encode()
            return r

        http.get.side_effect = changed
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, http)
        h = DataHarvest.objects.get()
        self.assertEqual((h.next_url, h.records, h.status), (before, 20, "failed"))
        self.assertEqual(DataSnapshot.objects.count(), 2)

    def test_configuration_dry_run_idempotence_preserves_admin(self):
        self.channel.delete()
        out = io.StringIO()
        call_command("configure_pig_spatial", stdout=out)
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_pig_spatial", apply=True, stdout=out)
        call_command("configure_pig_spatial", apply=True, stdout=out)
        self.assertEqual(DataChannel.objects.count(), 3)
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())
        c = DataChannel.objects.get(slug=slug("haldy"))
        c.config = {"admin": True}
        c.save()
        with self.assertRaises(CommandError):
            call_command("configure_pig_spatial", apply=True, stdout=out)
        c.refresh_from_db()
        self.assertEqual(c.config, {"admin": True})
