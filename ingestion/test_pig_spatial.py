import io
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.data_views import export_payload
from ingestion.data_coverage import coverage_report
from ingestion.data_paging import advance_harvest
from ingestion.data_service import collect, run_data
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.pig_paging import manifest
from ingestion.pig_spatial import PROFILES, channel_url, config, slug
from ingestion.spatial_query import query
from ingestion.spatial_topology import report as topology_report
from ingestion.transport import FetchError, Response
from ingestion.worker_stop import STOP

BASE_MWP = "https://cbdgmapa.pgi.gov.pl/arcgis/rest/services/hydrogeologia/mwp/MapServer/0/query"


class PigSpatialTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test.xlsx", sha256="e" * 64, sheets={})
        for rid in [36, 63, 64]:
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

    def http(self, key="jcwpd", geometry=None):
        http = Mock()
        kind = geometry or PROFILES[key]["geometry"]

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
                                "geometry": (
                                    {"type": "Point", "coordinates": [15 + i / 10, 50.5]}
                                    if kind == "Point"
                                    else {
                                        "type": "Polygon",
                                        "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]],
                                    }
                                ),
                            }
                            for i in ids
                        ],
                    }
            else:
                data = {
                    "name": PROFILES[key]["title"],
                    "geometryType": "esriGeometry" + PROFILES[key]["geometry"],
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

    def test_geosrodowisko_profiles_beyond_heaps_are_distinct_r064_layers(self):
        keys = ["haldy", "haldypkt", "crgp", "moek", "prognozy", "negatywne"]
        self.assertEqual(
            sorted(k for k, p in PROFILES.items() if p["registry"] == 64), sorted(keys)
        )
        targets = [(p["path"], p["layer"]) for p in PROFILES.values()]
        self.assertEqual(len(targets), len(set(targets)))
        self.assertEqual(PROFILES["crgp"]["title"], "CRGP - geostanowiska")
        self.assertEqual(PROFILES["haldypkt"]["geometry"], "Point")
        self.assertEqual(PROFILES["negatywne"]["geometry"], "Polygon")
        for key in keys:
            self.assertTrue(PROFILES[key]["path"].startswith("geosrodowisko/"))
            self.assertNotIn("max_ids", PROFILES[key])

    def test_mwp_live_layer_name_and_null_unique_id_info(self):
        self.assertEqual(PROFILES["mwp"]["title"], "MWP - monitoring wód podziemnych")
        c = DataChannel(kind="geojson", region="PL", url=channel_url("mwp"), config=config("mwp"))
        for unique, ok in [(None, True), ({"OIDFieldContainsHashValue": True}, False)]:
            http = self.http("mwp")
            original = http.get.side_effect

            def get(url, *args, unique=unique, original=original, **kwargs):
                response = original(url, *args, **kwargs)
                if url.endswith("?f=json"):
                    data = json.loads(response.body)
                    data["uniqueIdInfo"] = unique
                    response = Response(200, json.dumps(data).encode(), {}, url)
                return response

            http.get.side_effect = get
            with self.subTest(unique=unique):
                if ok:
                    self.assertEqual(collect(c, http)[2]["collector"], "pig_spatial_scan")
                else:
                    with self.assertRaises(FetchError):
                        collect(c, http)

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
        self.assertEqual(DataChannel.objects.count(), 10)
        self.assertEqual(DataChannel.objects.get(slug=slug("cbdg")).interval_minutes, 43200)
        self.assertEqual(DataChannel.objects.get(slug=slug("mwp")).interval_minutes, 1440)
        self.assertFalse(DataChannel.objects.filter(enabled=True).exists())
        c = DataChannel.objects.get(slug=slug("haldy"))
        c.config = {"admin": True}
        c.save()
        with self.assertRaises(CommandError):
            call_command("configure_pig_spatial", apply=True, stdout=out)
        c.refresh_from_db()
        self.assertEqual(c.config, {"admin": True})


class PigPointScanTests(TransactionTestCase):
    """Spatial query and topology open their own REPEATABLE READ transactions."""

    setUp = PigSpatialTests.setUp
    http = PigSpatialTests.http

    def mwp_channel(self):
        return DataChannel.objects.create(
            registry=RegistryEntry.objects.get(registry_id=63),
            slug=slug("mwp"),
            name="MWP",
            kind="geojson",
            region="PL",
            url=channel_url("mwp"),
            config=config("mwp"),
            access_approved=True,
        )

    def test_mwp_points_complete_verify_and_search(self):
        channel = self.mwp_channel()
        self.assertEqual(channel.url, BASE_MWP)
        result = advance_harvest(channel, self.http("mwp"))
        self.assertEqual((result["records"], result["status"]), (25, "complete"))
        harvest = DataHarvest.objects.get(channel=channel)
        harvest.channel = channel
        report = coverage_report(harvest, harvest.finished_at, True)
        self.assertEqual(report["payload_check"], "verified_available")
        self.assertTrue(report["scope_verified"])
        for method in ["geometry", "envelope"]:
            found, _, _ = query(channel.pk, [15.05, 50, 15.35, 51], method=method)
            self.assertEqual([f["id"] for f in found["features"]], [1, 2, 3])
        topology = topology_report(channel.pk)
        self.assertEqual((topology["features_checked"], topology["invalid_features"]), (25, 0))
        data = json.loads(export_payload(DataSnapshot.objects.get(channel=channel)))
        self.assertIn("ostatnim pomiarem", data["geoklimat_provenance"]["scope_note"])

    def test_geometry_type_is_pinned_per_profile(self):
        channel = self.mwp_channel()
        with self.assertRaises(FetchError):
            advance_harvest(channel, self.http("mwp", geometry="Polygon"))
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http("jcwpd", geometry="Point"))
        self.assertFalse(DataSnapshot.objects.exists())


class PigBoreholeScanTests(TestCase):
    setUp = PigSpatialTests.setUp
    http = PigSpatialTests.http

    def cbdg_channel(self):
        return DataChannel.objects.create(
            registry=RegistryEntry.objects.get(registry_id=36),
            slug=slug("cbdg"),
            name="CBDG",
            kind="geojson",
            region="PL",
            url=channel_url("cbdg"),
            config=config("cbdg"),
        )

    def kinds(self, http):
        out = []
        for call in http.get.call_args_list:
            q = parse_qs(urlsplit(call.args[0]).query)
            out.append(
                "ids"
                if q.get("returnIdsOnly")
                else "count"
                if q.get("returnCountOnly")
                else "page"
                if "objectIds" in q
                else "schema"
            )
        return out

    def test_ten_pages_per_call_with_count_checks_and_full_manifest_at_end(self):
        with patch.dict(PROFILES["cbdg"], {"limit": 2}):
            channel = self.cbdg_channel()
            http = self.http("cbdg")
            first = advance_harvest(channel, http)
            self.assertEqual((first["pages"], first["records"]), (10, 20))
            kinds = self.kinds(http)
            self.assertEqual((kinds.count("ids"), kinds.count("page")), (1, 10))
            self.assertEqual(kinds.count("count"), 1 + 10)
            http = self.http("cbdg")
            last = advance_harvest(channel, http)
            self.assertEqual((last["status"], last["pages"], last["records"]), ("complete", 13, 25))
            kinds = self.kinds(http)
            self.assertEqual((kinds.count("ids"), kinds.count("page")), (2, 3))

    def test_count_change_between_pages_keeps_cursor(self):
        with patch.dict(PROFILES["cbdg"], {"limit": 2}):
            channel = self.cbdg_channel()
            http = self.http("cbdg")
            get = http.get.side_effect
            calls = {"count": 0}

            def drift(url):
                r = get(url)
                if parse_qs(urlsplit(url).query).get("returnCountOnly"):
                    calls["count"] += 1
                    if calls["count"] == 4:
                        r.body = json.dumps({"count": 26}).encode()
                return r

            http.get.side_effect = drift
            with self.assertRaises(FetchError):
                advance_harvest(channel, http)
            h = DataHarvest.objects.get()
            self.assertEqual((h.status, h.pages, h.records), ("failed", 2, 4))

    def test_stop_request_ends_call_after_committed_page_and_resumes(self):
        with patch.dict(PROFILES["cbdg"], {"limit": 2}):
            channel = self.cbdg_channel()
            http = self.http("cbdg")
            get = http.get.side_effect
            pages = {"n": 0}

            def stop_at_third(url):
                if "objectIds" in parse_qs(urlsplit(url).query):
                    pages["n"] += 1
                    if pages["n"] == 3:
                        STOP.set()
                return get(url)

            http.get.side_effect = stop_at_third
            try:
                first = advance_harvest(channel, http)
            finally:
                STOP.clear()
            self.assertEqual(
                (first["status"], first["pages"], first["records"]), ("collecting", 3, 6)
            )
            last = advance_harvest(channel, self.http("cbdg"))
            self.assertEqual((last["status"], last["pages"], last["records"]), ("complete", 13, 25))

    def test_stop_request_skips_remaining_channels(self):
        self.cbdg_channel()
        DataChannel.objects.update(enabled=True, access_approved=True)
        http = self.http("cbdg")
        STOP.set()
        try:
            with patch("ingestion.data_service.Client", lambda: http):
                self.assertEqual(run_data(), [])
        finally:
            STOP.clear()
        http.get.assert_not_called()

    def test_transient_provider_error_retries_scan_within_hours_not_interval(self):
        with patch.dict(PROFILES["cbdg"], {"limit": 2}):
            channel = self.cbdg_channel()
            channel.enabled = channel.access_approved = True
            channel.interval_minutes = 43200
            channel.save()
            advance_harvest(channel, self.http("cbdg"))
            http = self.http("cbdg")
            get = http.get.side_effect

            def failing(url):
                if "objectIds" in parse_qs(urlsplit(url).query):
                    raise FetchError("Unexpected HTTP response", 503)
                return get(url)

            http.get.side_effect = failing
            before = timezone.now()
            with patch("ingestion.data_service.Client", lambda: http):
                self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "failed")
            channel.refresh_from_db()
            self.assertLess(channel.next_fetch_at, before + timedelta(hours=2))
            self.assertEqual(DataHarvest.objects.get().status, "failed")
            http.get.side_effect = lambda url: (_ for _ in ()).throw(
                FetchError("Unexpected HTTP response", 404)
            )
            with patch("ingestion.data_service.Client", lambda: http):
                run_data(channel.slug, force=True)
            channel.refresh_from_db()
            self.assertGreater(channel.next_fetch_at, before + timedelta(days=29))

    def test_raised_id_budget_only_for_boreholes(self):
        self.ids = list(range(1, 100002))
        ids, _ = manifest(self.cbdg_channel(), self.http("cbdg"))
        self.assertEqual(len(ids), 100001)
        mwp = DataChannel(kind="geojson", region="PL", url=channel_url("mwp"), config=config("mwp"))
        with self.assertRaises(FetchError):
            manifest(mwp, self.http("mwp"))
