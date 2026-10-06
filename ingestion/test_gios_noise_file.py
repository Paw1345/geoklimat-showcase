import io
import json
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ingestion.gios_noise_file import CONFIG, SIZE, SLUG, URL, audit_file
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response


class GiosNoiseFileTests(TestCase):
    def mock_client(self, change=None, status=206):
        dataset = {
            "type": "dataset",
            "id": "1404",
            "attributes": {
                "license_name": "CC BY 4.0",
                "license_condition_default_cc40": True,
            },
            "relationships": {"institution": {"data": {"type": "institution", "id": "100"}}},
        }
        resource = {
            "type": "resource",
            "id": "989426",
            "attributes": {
                "link": URL,
                "file_size": SIZE,
                "format": "xls",
                "contains_protected_data": False,
                "title": "Wyniki pomiarów hałasu drogowego w 2024 roku",
            },
            "relationships": {"dataset": {"data": {"type": "dataset", "id": "1404"}}},
        }
        if change:
            change(dataset, resource)
        client = Mock()

        def get(url, **kwargs):
            if url == URL:
                return Response(
                    status,
                    bytes.fromhex("d0cf11e0a1b11ae1") + bytes(1016),
                    {"content-range": f"bytes 0-1023/{SIZE}", "etag": '"version"'},
                    url,
                )
            row = dataset if "datasets/" in url else resource
            return Response(200, json.dumps(row and {"data": row}).encode(), {}, url)

        client.get.side_effect = get
        return client

    def test_bounded_access_audit(self):
        client = self.mock_client()
        result = audit_file(client)
        self.assertEqual(result["total_bytes"], SIZE)
        self.assertEqual(result["sample_bytes"], 1024)
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(client.get.call_args.kwargs["headers"], {"Range": "bytes=0-1023"})
        self.assertFalse(DataSnapshot.objects.exists())

    def test_changed_licence_provenance_or_file_rejected(self):
        changes = [
            lambda d, r: d["attributes"].update(license_name="other"),
            lambda d, r: d["attributes"].update(license_condition_custom_description="restriction"),
            lambda d, r: d["relationships"]["institution"]["data"].update(id="23"),
            lambda d, r: r["relationships"]["dataset"]["data"].update(id="2"),
            lambda d, r: r["attributes"].update(link="https://example.com/file.xls"),
            lambda d, r: r["attributes"].update(file_size=1),
            lambda d, r: r["attributes"].update(contains_protected_data=True),
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(FetchError):
                audit_file(self.mock_client(change))
        with self.assertRaises(FetchError):
            audit_file(self.mock_client(status=200))

    def test_configuration_dry_run_idempotent_and_preserves_admin(self):
        release = RegistryRelease.objects.create(filename="test", sha256="e" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=48,
            release=release,
            master={},
            technical={},
            integration_state="needs_connector",
        )
        with patch(
            "ingestion.management.commands.configure_gios_noise_file.Client",
            return_value=self.mock_client(),
        ):
            call_command("configure_gios_noise_file", stdout=io.StringIO())
            self.assertFalse(DataChannel.objects.exists())
            call_command("configure_gios_noise_file", apply=True, stdout=io.StringIO())
            call_command("configure_gios_noise_file", apply=True, stdout=io.StringIO())
            channel = DataChannel.objects.get(slug=SLUG)
            self.assertEqual(channel.config, CONFIG)
            self.assertTrue(channel.access_approved)
            self.assertFalse(channel.enabled)
            self.assertFalse(DownloadJob.objects.exists())
            entry.refresh_from_db()
            self.assertEqual(entry.integration_state, "needs_connector")
            channel.url = "https://example.com/admin"
            channel.save()
            with self.assertRaises(CommandError):
                call_command("configure_gios_noise_file", apply=True, stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertEqual(channel.url, "https://example.com/admin")
