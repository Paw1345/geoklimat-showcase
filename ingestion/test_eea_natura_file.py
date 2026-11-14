import io
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.downloads import CHUNK_BYTES, advance_download, enqueue, file_limit
from ingestion.eea_natura_file import CONFIG, ETAG, MAX_FILE_BYTES, SIZE, SLUG, URL, audit_file
from ingestion.file_exports import export_descriptor
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

COMMAND = "ingestion.management.commands.configure_eea_natura_file.Client"


def sample(start=0, length=1024, total=SIZE, etag=ETAG, status=206, body=None):
    body = b"SITECODE,SITENAME\n" + b"x" * (length - 18) if body is None else body
    return Response(
        status,
        body,
        {"content-range": f"bytes {start}-{start + len(body) - 1}/{total}", "etag": etag},
        URL,
    )


class EeaNaturaFileTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="d" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=176,
            release=release,
            master={},
            technical={},
            integration_state="configured",
        )

    def client_for(self, response):
        client = Mock()
        client.get.return_value = response
        return client

    def test_single_range_audit_pins_size_and_version(self):
        client = self.client_for(sample())
        result = audit_file(client)
        self.assertEqual((result["total_bytes"], result["etag"]), (SIZE, ETAG))
        self.assertEqual(client.get.call_count, 1)
        self.assertEqual(client.get.call_args.kwargs["headers"], {"Range": "bytes=0-1023"})
        self.assertGreater(SIZE, settings.DATA_MANUAL_MAX_BYTES)
        self.assertLessEqual(SIZE, MAX_FILE_BYTES)
        self.assertIn("sdi.eea.europa.eu", settings.INGEST_ALLOWED_HOSTS)
        for response in (
            sample(total=SIZE + 1),
            sample(etag='"other"'),
            sample(body=b"\x00" * 1024),
            sample(status=200),
        ):
            with self.subTest(response=response), self.assertRaises(FetchError):
                audit_file(self.client_for(response))

    def test_configuration_creates_approved_eu_channel_with_named_limit(self):
        with patch(COMMAND, return_value=self.client_for(sample())):
            call_command("configure_eea_natura_file", stdout=io.StringIO())
            self.assertFalse(DataChannel.objects.exists())
            call_command("configure_eea_natura_file", apply=True, stdout=io.StringIO())
            call_command("configure_eea_natura_file", apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get(slug=SLUG)
        self.assertEqual((channel.region, channel.kind, channel.config), ("EU", "file", CONFIG))
        self.assertTrue(channel.access_approved)
        self.assertFalse(channel.enabled)
        self.assertEqual(file_limit(channel), MAX_FILE_BYTES)
        self.assertFalse(DownloadJob.objects.exists())
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.integration_state, "configured")
        channel.config = {**CONFIG, "max_file_bytes": 15_000_000_000}
        channel.save()
        with patch(COMMAND, return_value=self.client_for(sample())):
            with self.assertRaises(CommandError):
                call_command("configure_eea_natura_file", apply=True, stdout=io.StringIO())

    def test_first_chunk_passes_named_limit_and_export_is_csv(self):
        with patch(COMMAND, return_value=self.client_for(sample())):
            call_command("configure_eea_natura_file", apply=True, stdout=io.StringIO())
        channel = DataChannel.objects.get(slug=SLUG)
        job, _ = enqueue(channel)
        advance_download(job, self.client_for(sample(length=CHUNK_BYTES)))
        job.refresh_from_db()
        self.assertEqual((job.status, job.offset, job.total_bytes), ("running", CHUNK_BYTES, SIZE))
        self.assertEqual(DataSnapshot.objects.filter(download_job=job).count(), 1)
        descriptor = export_descriptor(channel, job)
        self.assertEqual(descriptor["media_type"], "text/csv; charset=utf-8")
        self.assertTrue(descriptor["filename"].endswith("NATURA2000SITES.csv"))
        staff = get_user_model().objects.create_user("natura-reader", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(f"/data/files/{job.pk}/download/").status_code, 404)

    def test_same_file_without_named_limit_is_blocked_before_any_chunk(self):
        channel = DataChannel.objects.create(
            registry=self.entry,
            slug="unnamed-limit",
            name="x",
            kind="file",
            region="EU",
            url=URL,
            config={key: value for key, value in CONFIG.items() if key != "max_file_bytes"},
            access_approved=True,
        )
        job, _ = enqueue(channel)
        with self.assertRaises(FetchError):
            advance_download(job, self.client_for(sample(length=CHUNK_BYTES)))
        self.assertFalse(DataSnapshot.objects.exists())
