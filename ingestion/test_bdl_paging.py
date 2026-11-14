import io
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.bdl_paging import PROFILES, channel_url, config, slug
from ingestion.bdl_probe import LICENSE, OPENAPI
from ingestion.data_paging import advance_harvest
from ingestion.data_service import run_data
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

API = {
    "info": {
        "title": "Usługa OGC API Features Banku Danych o Lasach",
        "contact": {"name": "Bank Danych o Lasach"},
        "license": {"url": LICENSE},
        "termsOfService": LICENSE,
    }
}
POLYGON = {"type": "Polygon", "coordinates": [[[15, 50], [16, 50], [16, 51], [15, 50]]]}


class BdlPagingTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t.xlsx", sha256="f" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            release=release,
            registry_id=39,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        self.count, self.year = 5, 2026
        self.limit_patch = patch.dict(
            PROFILES,
            {
                "nadlesnictwa": {"limit": 2, "geometry": True, "max_count": 1000},
                "RDLP_Krosno_wydzielenia": {"limit": 2, "geometry": False, "max_count": 10},
            },
        )
        self.limit_patch.start()
        self.addCleanup(self.limit_patch.stop)
        self.channel = self.make("RDLP_Krosno_wydzielenia")

    def make(self, collection, **extra):
        return DataChannel.objects.create(
            registry=self.entry,
            slug=slug(collection),
            kind="geojson",
            region="PL",
            url=channel_url(collection),
            config=config(collection),
            enabled=True,
            access_approved=True,
            **extra,
        )

    def http(self, geometry=False, mutate=None):
        http = Mock()
        http.max_bytes = 5 * 1024 * 1024

        def get(url):
            if url == OPENAPI:
                return Response(200, json.dumps(API).encode(), {}, url)
            params = parse_qs(urlsplit(url).query)
            limit = int(params["limit"][0])
            offset = int(params.get("offset", ["0"])[0])
            ids = list(range(offset + 1, min(offset + limit, self.count) + 1))
            links = [{"rel": "self", "href": url}]
            if ids and ids[-1] < self.count:
                links.append({"rel": "next", "href": f"x?offset={ids[-1]}&limit={limit}"})
            data = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": i,
                        "properties": {"a_year": self.year, "adr_for": f"04-{i}"},
                        "geometry": POLYGON if geometry and "skipGeometry" not in url else None,
                    }
                    for i in ids
                ],
                "numberMatched": self.count,
                "numberReturned": len(ids),
                "links": links,
            }
            if mutate:
                mutate(url, data)
            return Response(200, json.dumps(data).encode(), {}, url)

        http.get.side_effect = get
        return http

    def test_attribute_scan_resumes_and_covers_every_id_once(self):
        first = advance_harvest(self.channel, self.http())
        self.assertEqual((first["status"], first["pages"], first["records"]), ("collecting", 2, 4))
        last = advance_harvest(self.channel, self.http())
        self.assertEqual(
            (last["status"], last["records"], last["source_count"]), ("complete", 5, 5)
        )
        snapshots = DataSnapshot.objects.order_by("pk")
        ids = [f["id"] for s in snapshots for f in json.loads(bytes(s.payload))["features"]]
        self.assertEqual(ids, [1, 2, 3, 4, 5])
        self.assertTrue(all("skipGeometry=true" in s.summary["request_url"] for s in snapshots))
        self.assertEqual(snapshots[0].media_type, "application/geo+json")

    def test_original_bytes_are_stored(self):
        http = self.http()
        advance_harvest(self.channel, http)
        snapshot = DataSnapshot.objects.order_by("pk").first()
        raw = http.get.side_effect(snapshot.summary["request_url"]).body
        self.assertEqual(bytes(snapshot.payload), raw)

    def test_geometry_layer_requires_valid_non_null_polygons(self):
        channel = self.make("nadlesnictwa")
        result = advance_harvest(channel, self.http(geometry=True))
        self.assertEqual(result["records"], 4)

        def wrong_type(url, data):
            if "offset=0" in url:
                data["features"][0]["geometry"] = {"type": "Point", "coordinates": [15, 50]}

        broken = self.http(geometry=True, mutate=wrong_type)
        other = self.make("lesnictwa")
        with self.assertRaises(FetchError):
            advance_harvest(other, broken)

    def test_count_change_preserves_cursor(self):
        advance_harvest(self.channel, self.http())
        self.count = 6
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http())
        harvest = DataHarvest.objects.get()
        self.assertEqual((harvest.status, harvest.records), ("failed", 4))

    def test_bad_geometry_names_feature_preserves_page_and_can_resume(self):
        self.count = 7
        channel = self.make("nadlesnictwa")
        for geometry in (
            "missing",
            {"type": "Point", "coordinates": [15, 50]},
            {"type": "Polygon", "coordinates": []},
        ):
            with self.subTest(geometry=geometry):
                DataSnapshot.objects.filter(channel=channel).delete()
                DataHarvest.objects.filter(channel=channel).delete()
                advance_harvest(channel, self.http(geometry=True))
                harvest = DataHarvest.objects.get(channel=channel)
                before = (harvest.next_url, harvest.config_hash, harvest.size_bytes)
                copies = list(
                    DataSnapshot.objects.filter(channel=channel)
                    .order_by("pk")
                    .values_list("pk", "sha256", "payload")
                )

                def missing(url, data, geometry=geometry):
                    if "offset=4" in url:
                        if geometry == "missing":
                            data["features"][1].pop("geometry")
                        else:
                            data["features"][1]["geometry"] = geometry

                with self.assertRaisesRegex(FetchError, "BDL nadlesnictwa feature 6:"):
                    advance_harvest(channel, self.http(geometry=True, mutate=missing))
                harvest.refresh_from_db()
                self.assertEqual((harvest.status, harvest.pages, harvest.records), ("failed", 2, 4))
                self.assertEqual(
                    (harvest.next_url, harvest.config_hash, harvest.size_bytes), before
                )
                self.assertEqual(
                    list(
                        DataSnapshot.objects.filter(channel=channel)
                        .order_by("pk")
                        .values_list("pk", "sha256", "payload")
                    ),
                    copies,
                )
                result = advance_harvest(channel, self.http(geometry=True))
                self.assertEqual((result["status"], result["records"]), ("complete", 7))
                ids = [
                    f["id"]
                    for s in DataSnapshot.objects.filter(channel=channel).order_by("pk")
                    for f in json.loads(bytes(s.payload))["features"]
                ]
                self.assertEqual(ids, list(range(1, 8)))

    def test_explicit_null_preserved_with_page_geometry_counts(self):
        channel = self.make("nadlesnictwa")
        original_config = dict(channel.config)

        def missing(url, data):
            if "skipGeometry" not in url:
                for feature in data["features"]:
                    if feature["id"] in (2, 3):
                        feature["geometry"] = None

        http = self.http(geometry=True, mutate=missing)
        first = advance_harvest(channel, http)
        self.assertEqual((first["pages"], first["records"]), (2, 4))
        harvest = DataHarvest.objects.get(channel=channel)
        original_scope = harvest.config_hash
        last = advance_harvest(channel, http)
        self.assertEqual((last["status"], last["records"]), ("complete", 5))
        copies = list(DataSnapshot.objects.filter(channel=channel).order_by("pk"))
        for copy, missing_ids in zip(copies, ([2], [3], []), strict=True):
            summary = copy.summary
            self.assertEqual(summary["geometry_policy"], "bdl-null-reported-v1")
            self.assertTrue(summary["geometry_requested"])
            self.assertEqual(summary["geometry_missing_ids"], missing_ids)
            self.assertEqual(summary["geometry_missing_records"], len(missing_ids))
            self.assertEqual(
                summary["geometry_present_records"], summary["records"] - len(missing_ids)
            )
            self.assertEqual(summary["geometry_included"], not missing_ids)
            self.assertEqual(bytes(copy.payload), http.get.side_effect(summary["request_url"]).body)
        ids = [f["id"] for s in copies for f in json.loads(bytes(s.payload))["features"]]
        self.assertEqual(ids, list(range(1, 6)))
        channel.refresh_from_db()
        harvest.refresh_from_db()
        self.assertEqual(channel.config, original_config)
        self.assertEqual(harvest.config_hash, original_scope)

    def test_all_null_geometry_page_keeps_every_record_without_geometry_claim(self):
        channel = self.make("lesnictwa")
        result = advance_harvest(channel, self.http(geometry=False))
        self.assertEqual((result["status"], result["records"]), ("complete", 5))
        copy = DataSnapshot.objects.get(channel=channel)
        self.assertEqual(copy.summary["geometry_present_records"], 0)
        self.assertEqual(copy.summary["geometry_missing_records"], 5)
        self.assertEqual(copy.summary["geometry_missing_ids"], [1, 2, 3, 4, 5])
        self.assertFalse(copy.summary["geometry_included"])
        self.assertTrue(
            all(f["geometry"] is None for f in json.loads(bytes(copy.payload))["features"])
        )

    def test_attribute_page_does_not_claim_missing_requested_geometry(self):
        advance_harvest(self.channel, self.http())
        summary = DataSnapshot.objects.order_by("pk").first().summary
        self.assertFalse(summary["geometry_requested"])
        self.assertFalse(summary["geometry_included"])
        self.assertNotIn("geometry_missing_records", summary)
        self.assertNotIn("geometry_policy", summary)

    def test_id_gap_or_year_change_rejected_without_copy(self):
        def gap(url, data):
            if "offset=0" in url and "limit=2" in url:
                data["features"][1]["id"] = 7

        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http(mutate=gap))
        self.assertEqual(DataSnapshot.objects.count(), 0)

        def year(url, data):
            if "limit=2" in url:
                data["features"][0]["properties"]["a_year"] = 2025

        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http(mutate=year))
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_short_page_or_wrong_next_rejected(self):
        def short(url, data):
            if "limit=2" in url:
                data["features"] = data["features"][:1]

        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http(mutate=short))

        def wrong_next(url, data):
            for link in data["links"]:
                if link["rel"] == "next":
                    link["href"] = "x?offset=9&limit=2"

        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http(mutate=wrong_next))
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_attribute_scan_rejects_geometry(self):
        def add_geometry(url, data):
            if "limit=2" in url:
                data["features"][0]["geometry"] = POLYGON

        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http(mutate=add_geometry))

    def test_license_change_blocks_scan(self):
        http = self.http()
        get = http.get.side_effect

        def changed(url):
            if url == OPENAPI:
                body = json.dumps(
                    {"info": {**API["info"], "termsOfService": "https://example.org"}}
                ).encode()
                return Response(200, body, {}, url)
            return get(url)

        http.get.side_effect = changed
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, http)
        self.assertEqual(DataSnapshot.objects.count(), 0)

    def test_modified_profile_rejected(self):
        self.channel.url += "?bbox=1,2,3,4"
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, self.http())

    def test_dry_run_audit_probes_first_page_without_writes(self):
        with patch("ingestion.data_service.Client", return_value=self.http()):
            result = run_data(self.channel.slug, dry_run=True)[0]
        self.assertEqual((result["status"], result["records"]), ("dry_run", 2))
        self.assertEqual(DataSnapshot.objects.count(), 0)
        self.assertEqual(DataHarvest.objects.count(), 0)

    def test_configure_creates_disabled_channels_and_keeps_existing(self):
        out = io.StringIO()
        call_command("configure_bdl_scans", stdout=out)
        self.assertIn("Dry-run", out.getvalue())
        self.assertEqual(DataChannel.objects.count(), 1)
        call_command("configure_bdl_scans", "--apply", stdout=io.StringIO())
        created = DataChannel.objects.exclude(pk=self.channel.pk)
        self.assertEqual(created.count(), len(PROFILES) - 1)
        self.assertFalse(created.filter(enabled=True).exists())
        self.assertFalse(created.filter(access_approved=True).exists())
        self.assertEqual({c.interval_minutes for c in created}, {43200})
        self.channel.refresh_from_db()
        self.assertTrue(self.channel.enabled)
        self.channel.config = {**self.channel.config, "feature_limit": 5}
        self.channel.save()
        with self.assertRaises(CommandError):
            call_command("configure_bdl_scans", "--apply", stdout=io.StringIO())

    def test_configure_updates_page_size_only_on_unused_disabled_scan(self):
        old = {**config("lesnictwa"), "feature_limit": 50}
        channel = self.make("lesnictwa")
        DataChannel.objects.filter(pk=channel.pk).update(
            config=old, enabled=False, access_approved=False
        )
        call_command("configure_bdl_scans", stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertEqual(channel.config, old)
        out = io.StringIO()
        call_command("configure_bdl_scans", "--apply", stdout=out)
        self.assertIn("update page size", out.getvalue())
        channel.refresh_from_db()
        self.assertEqual(channel.config, config("lesnictwa"))
        self.assertFalse(channel.enabled or channel.access_approved)
        DataChannel.objects.filter(pk=channel.pk).update(config=old)
        DataHarvest.objects.create(channel=channel, config_hash="x", start_url="u", next_url="u")
        with self.assertRaises(CommandError):
            call_command("configure_bdl_scans", "--apply", stdout=io.StringIO())
