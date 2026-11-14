import json
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from ingestion.jrc_catalogue import CONFIG, SLUG, URL
from ingestion.jrc_files import FIELDS, distributions, file_channel, slug_for
from ingestion.models import (
    DataChannel,
    DownloadJob,
    Item,
    RegistryEntry,
    RegistryRelease,
    Source,
)
from ingestion.transport import FetchError, Response

DATASET = "http://data.europa.eu/89h/abc-123"
FILE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/X/data.csv"
CC = "http://creativecommons.org/licenses/by/4.0/"


def uri(value):
    return {"type": "uri", "value": value}


def literal(value):
    return {"type": "literal", "value": value}


class JrcFilesTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t", sha256="b" * 64, sheets={})
        RegistryEntry.objects.create(
            release=release, registry_id=181, master={}, technical={}, integration_state="x"
        )
        self.rows = [
            {
                "distribution": uri("http://data.europa.eu/89h/abc-123/d1"),
                "download": uri(FILE),
                "license": uri(CC),
                "format": literal("CSV"),
                "size": {"type": "typed-literal", "value": "1024"},
                "title": literal("Table"),
            },
            {
                "distribution": uri("http://data.europa.eu/89h/abc-123/d2"),
                "access": uri("https://data.jrc.ec.europa.eu/viewer"),
                "dataset_license": uri(CC),
            },
            {
                "distribution": uri("http://data.europa.eu/89h/abc-123/d3"),
                "download": uri("https://example.org/x.zip"),
                "license": uri(
                    "http://publications.europa.eu/resource/authority/licence/COM_REUSE"
                ),
                "size": literal(str(60 * 1024 * 1024)),
            },
        ]

    def sparql(self):
        client = Mock()
        client.get.side_effect = lambda url, headers: Response(
            200,
            json.dumps({"head": {"vars": FIELDS}, "results": {"bindings": self.rows}}).encode(),
            {},
            url,
        )
        return client

    def test_only_single_cc_by_file_on_allowlisted_host_is_eligible(self):
        files = distributions(self.sparql(), DATASET)
        self.assertEqual([f["eligible"] for f in files], [True, False, False])
        self.assertEqual((files[0]["file_url"], files[0]["size_bytes"]), (FILE, 1024))
        self.assertIn("brak jednego adresu pliku (downloadURL)", files[1]["reasons"])
        self.assertEqual(files[1]["licence"], CC.rstrip("/"))
        self.assertEqual(len(files[2]["reasons"]), 3)

    def test_outside_catalogue_identity_or_schema_fails_closed(self):
        client = self.sparql()
        with self.assertRaises(FetchError):
            distributions(client, "http://example.org/dataset")
        client.get.assert_not_called()
        self.rows[0]["unexpected"] = literal("x")
        with self.assertRaises(FetchError):
            distributions(self.sparql(), DATASET)

    def test_file_channel_approved_idempotent_and_preserves_admin(self):
        files = distributions(self.sparql(), DATASET)
        channel = file_channel(DATASET, "Soil data", files[0])
        self.assertEqual((channel.kind, channel.region, channel.url), ("file", "EU", FILE))
        self.assertTrue(channel.access_approved)
        self.assertFalse(channel.enabled)
        self.assertNotIn("max_file_bytes", channel.config)
        self.assertEqual(file_channel(DATASET, "Soil data", files[0]).pk, channel.pk)
        channel.config = {**channel.config, "max_file_bytes": 10**9}
        channel.save()
        with self.assertRaises(FetchError):
            file_channel(DATASET, "Soil data", files[0])
        with self.assertRaises(FetchError):
            file_channel(DATASET, "Soil data", files[1])

    def test_staff_lists_and_requests_one_file_by_position(self):
        source = Source.objects.create(slug=SLUG, adapter="json", url=URL, config=CONFIG)
        item = Item.objects.create(
            source=source,
            external_id=DATASET,
            title="Soil data",
            url="https://data.jrc.ec.europa.eu/dataset/abc-123",
            content_hash="c" * 64,
            metadata={"record_kind": "dataset_collection", "dataset_uri": DATASET},
        )
        staff = get_user_model().objects.create_user("staff", password="x", is_staff=True)
        self.client_http = self.sparql()
        web = self.client
        web.force_login(staff)
        with patch("ingestion.transport.Client", lambda: self.client_http):
            page = web.get(f"/data/jrc/{item.pk}/files/")
            self.assertContains(page, "Pobierz ten plik w tle")
            self.assertContains(page, "licencja inna niż CC BY 4.0")
            refused = web.post(f"/data/jrc/{item.pk}/files/2/request/")
            self.assertEqual(refused.status_code, 400)
            done = web.post(f"/data/jrc/{item.pk}/files/0/request/")
        channel = DataChannel.objects.get(slug=slug_for(FILE))
        self.assertRedirects(done, f"/data/{channel.pk}/", fetch_redirect_response=False)
        self.assertEqual(DownloadJob.objects.get().url, FILE)
        other = Item.objects.create(
            source=Source.objects.create(slug="other", adapter="json", url=URL, config={}),
            external_id="x",
            title="x",
            url="https://data.jrc.ec.europa.eu/x",
            content_hash="d" * 64,
            metadata={"record_kind": "dataset_collection", "dataset_uri": DATASET},
        )
        self.assertEqual(web.get(f"/data/jrc/{other.pk}/files/").status_code, 404)

    def test_directory_or_document_page_not_eligible(self):
        for url in [
            "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/EDO/GDO_Indicator/",
            "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/document/abc-123",
        ]:
            self.rows[0]["download"] = uri(url)
            first = distributions(self.sparql(), DATASET)[0]
            with self.subTest(url=url):
                self.assertFalse(first["eligible"])
                self.assertIn(
                    "adres nie wskazuje pliku danych (katalog lub strona)", first["reasons"]
                )

    @override_settings(INGEST_ALLOWED_HOSTS=["data.jrc.ec.europa.eu"])
    def test_host_outside_allowlist_not_eligible(self):
        self.assertFalse(distributions(self.sparql(), DATASET)[0]["eligible"])
