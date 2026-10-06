import io
import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlsplit

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion.data_paging import advance_harvest, initial_url, validate_page_url
from ingestion.data_service import query_url, run_data
from ingestion.models import DataChannel, DataHarvest, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response


class DataPagingTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="b" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            registry_id=53, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug="wfs",
            name="Test",
            kind="geojson",
            region="PL",
            access_approved=True,
            enabled=True,
            url="https://mapy.geoportal.gov.pl/wss/service/wfsGN/guest?VERSION=2.0.0&REQUEST=GetFeature&COUNT=1",
            config={"pagination": "wfs2", "feature_limit": 1},
        )

    def page(self, url, key, matched=3, links=None):
        data = {
            "type": "FeatureCollection",
            "numberMatched": matched,
            "features": []
            if key is None
            else [
                {
                    "type": "Feature",
                    "id": key,
                    "geometry": None,
                    "properties": {"datetime": timezone.now().isoformat()},
                }
            ],
            "links": links or [],
        }
        return Response(200, json.dumps(data).encode(), {}, url)

    def transport(self):
        client = Mock()
        client.get.side_effect = lambda url: self.page(
            url, int(dict(parse_qsl(urlsplit(url).query)).get("STARTINDEX", 0))
        )
        return client

    def test_wfs_resumes_and_finishes_without_restarting(self):
        client = self.transport()
        first = advance_harvest(self.channel, client)
        self.assertEqual((first["status"], first["pages"]), ("collecting", 2))
        harvest = DataHarvest.objects.get()
        self.assertIn("STARTINDEX=2", harvest.next_url)
        second = advance_harvest(DataChannel.objects.get(pk=self.channel.pk), self.transport())
        self.assertEqual((second["status"], second["pages"], second["records"]), ("complete", 3, 3))
        self.assertEqual(DataSnapshot.objects.count(), 3)
        self.assertContains(self.client_view(), "Postęp pobierania stron")

    def client_view(self):
        from django.test import Client

        return Client().get(f"/data/{self.channel.pk}/")

    def test_network_failure_keeps_last_committed_page(self):
        client = self.transport()
        url = initial_url(self.channel)
        client.get.side_effect = [self.page(url, 0), FetchError("offline")]
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, client)
        harvest = DataHarvest.objects.get()
        self.assertEqual((harvest.status, harvest.pages), ("failed", 1))
        result = advance_harvest(self.channel, self.transport())
        self.assertEqual((result["status"], result["pages"]), ("complete", 3))

    def test_quota_failure_does_not_advance_or_store(self):
        with override_settings(DATA_STORAGE_MAX_BYTES=1), self.assertRaises(FetchError):
            advance_harvest(self.channel, self.transport())
        self.assertEqual(DataHarvest.objects.get().pages, 0)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_cursor_save_failure_rolls_back_snapshot(self):
        url = initial_url(self.channel)
        from ingestion.data_paging import digest

        DataHarvest.objects.create(
            channel=self.channel,
            config_hash=digest(
                [self.channel.url, self.channel.kind, self.channel.region, self.channel.config]
            ),
            start_url=url,
            next_url=url,
        )
        original = DataHarvest.save

        def save(obj, *args, **kwargs):
            if obj.pages > 0:
                raise RuntimeError("simulated crash")
            return original(obj, *args, **kwargs)

        with patch.object(DataHarvest, "save", save), self.assertRaises(RuntimeError):
            advance_harvest(self.channel, self.transport())
        self.assertEqual(DataHarvest.objects.get().pages, 0)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_repeated_wfs_page_fails_instead_of_false_completion(self):
        client = Mock()
        client.get.side_effect = lambda url: self.page(url, "same")
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, client)
        self.assertEqual(DataHarvest.objects.get().pages, 1)
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_scope_and_origin_cannot_change(self):
        url = "https://stac.dataspace.copernicus.eu/v1/search?bbox=1,2,3,4&collections=a"
        for target in [
            url.replace("https:", "http:"),
            url.replace("/v1/", "/v2/"),
            url.replace("collections=a", "collections=b"),
            url.replace("stac.dataspace.copernicus.eu", "bad.example"),
            url + "#fragment",
        ]:
            with self.subTest(target=target), self.assertRaises(FetchError):
                validate_page_url(url, target, {"token"})

    def test_stac_get_paging_fixed_window_and_no_asset_requests(self):
        self.channel.kind = "stac"
        self.channel.url = "https://stac.dataspace.copernicus.eu/v1/search"
        self.channel.config = {
            "pagination": "stac_get",
            "bbox": [14, 49, 24, 55],
            "collection": "sentinel-2-l2a",
        }
        self.channel.save()
        client = Mock()

        def reply(url):
            token = dict(parse_qsl(urlsplit(url).query)).get("token")
            return self.page(
                url,
                token or "first",
                matched=2,
                links=[]
                if token
                else [{"rel": "next", "method": "GET", "href": query_url(url, token="second")}],
            )

        client.get.side_effect = reply
        result = advance_harvest(self.channel, client)
        self.assertEqual((result["status"], result["pages"]), ("complete", 2))
        queries = [
            dict(parse_qsl(urlsplit(call.args[0]).query)) for call in client.get.call_args_list
        ]
        self.assertEqual(queries[0]["datetime"], queries[1]["datetime"])
        self.assertEqual(client.get.call_count, 2)

    def test_stac_post_next_rejected_before_store(self):
        self.channel.kind = "stac"
        self.channel.url = "https://stac.dataspace.copernicus.eu/v1/search"
        self.channel.config = {
            "pagination": "stac_get",
            "bbox": [14, 49, 24, 55],
            "collection": "sentinel-2-l2a",
        }
        client = Mock()
        client.get.side_effect = lambda url: self.page(
            url, "first", links=[{"rel": "next", "method": "POST", "href": url}]
        )
        with self.assertRaises(FetchError):
            advance_harvest(self.channel, client)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_dry_run_does_not_create_job_or_pages(self):
        with patch("ingestion.data_service.Client", return_value=self.transport()):
            result = run_data(self.channel.slug, dry_run=True)
        self.assertEqual(result[0]["status"], "dry_run")
        self.assertFalse(DataHarvest.objects.exists())
        self.assertFalse(DataSnapshot.objects.exists())

    def test_changed_config_supersedes_old_scan(self):
        advance_harvest(self.channel, self.transport())
        self.channel.region = "EU"
        self.channel.save()
        advance_harvest(self.channel, self.transport())
        self.assertEqual(DataHarvest.objects.filter(status="superseded").count(), 1)
        self.assertEqual(DataHarvest.objects.filter(status="running").count(), 1)

    def test_access_exception_label(self):
        from django.test import Client

        entry = self.channel.registry
        entry.registry_id = 169
        entry.save()
        self.assertContains(Client().get("/registry/169/"), "Oczekuje na uprawnienia")

    def test_paging_configuration_dry_run_and_idempotence(self):
        for _slug, registry_id in [
            ("copernicus-scenes-pl", 191),
            ("copernicus-scenes-europe", 191),
            ("gugik-named-places", 53),
        ]:
            if registry_id == 191:
                RegistryEntry.objects.get_or_create(
                    registry_id=191,
                    defaults={
                        "release": self.channel.registry.release,
                        "master": {},
                        "technical": {},
                    },
                )
        # Seed also requires IMGW entries.
        for key in (33, 34):
            RegistryEntry.objects.create(
                registry_id=key, release=self.channel.registry.release, master={}, technical={}
            )
        call_command("seed_data_channels", stdout=io.StringIO())
        call_command("configure_data_paging", stdout=io.StringIO())
        channel = DataChannel.objects.get(slug="copernicus-scenes-pl")
        self.assertNotIn("pagination", channel.config)
        call_command("configure_data_paging", apply=True, stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertEqual(channel.config["pagination"], "stac_get")
        channel.enabled = channel.access_approved = True
        channel.save()
        call_command("configure_data_paging", apply=True, stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertTrue(channel.enabled)
