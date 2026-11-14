import io
from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from ingestion.jrc_files import audit_file, slug_for
from ingestion.models import DataChannel, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

FILE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/X/data.csv"
ETAG = '"abc"'


def partial(total=4096, etag=ETAG, url=FILE, status=206, body=b"a" * 1024):
    headers = {"content-range": f"bytes 0-{len(body) - 1}/{total}", "etag": etag}
    return Response(status, body, headers, url)


class JrcFileAuditTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t", sha256="b" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            release=release, registry_id=181, master={}, technical={}, integration_state="x"
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug=slug_for(FILE),
            name="JRC file",
            kind="file",
            url=FILE,
            config={
                "mode": "jrc_file",
                "dataset": "http://data.europa.eu/89h/abc-123",
                "distribution": "http://data.europa.eu/89h/abc-123/d1",
                "license": "http://creativecommons.org/licenses/by/4.0",
            },
            region="EU",
            attribution="JRC",
            access_approved=True,
            enabled=False,
        )

    def provider(self, response):
        client = Mock()
        client.get.return_value = response
        return client

    def job(self, total=4096, etag=ETAG):
        now = timezone.now()
        return DownloadJob.objects.create(
            channel=self.channel,
            url=FILE,
            config_hash="x",
            status="complete",
            expires_at=now + timedelta(days=30),
            offset=total,
            total_bytes=total,
            etag=etag,
            sha256="0" * 64,
        )

    def test_range_probe_matches_last_complete_copy(self):
        job = self.job()
        client = self.provider(partial())
        result = audit_file(client, self.channel)
        self.assertEqual(
            (result["status"], result["total_bytes"], result["matches_complete_job"]),
            ("verified", 4096, job.pk),
        )
        client.get.assert_called_once_with(
            FILE, headers={"Range": "bytes=0-1023"}, allow_partial=True
        )

    def test_changed_size_etag_redirect_or_foreign_channel_fails_closed(self):
        self.job()
        for response in [partial(total=5000), partial(etag='"new"'), partial(url=FILE + "x")]:
            with self.subTest(response=response.url), self.assertRaises(FetchError):
                audit_file(self.provider(response), self.channel)
        self.channel.config = {**self.channel.config, "license": "other"}
        client = self.provider(partial())
        with self.assertRaises(FetchError):
            audit_file(client, self.channel)
        client.get.assert_not_called()

    def test_file_over_limit_is_refused(self):
        with self.assertRaises(FetchError):
            audit_file(self.provider(partial(total=60 * 1024 * 1024)), self.channel)

    def test_command_dry_run_writes_nothing_and_apply_records_status_only(self):
        with patch("ingestion.management.commands.audit_jrc_file.Client") as factory:
            factory.return_value = self.provider(partial())
            call_command("audit_jrc_file", "--source", self.channel.slug, stdout=io.StringIO())
            self.channel.refresh_from_db()
            self.assertEqual(self.channel.last_status, "not_tested")
            call_command(
                "audit_jrc_file", "--source", self.channel.slug, "--apply", stdout=io.StringIO()
            )
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.last_status, "verified")
        self.assertIsNotNone(self.channel.checked_at)
        self.assertFalse(self.channel.enabled)
        self.assertFalse(DownloadJob.objects.exists())

    def test_command_failure_leaves_channel_unchanged(self):
        with patch("ingestion.management.commands.audit_jrc_file.Client") as factory:
            factory.return_value = self.provider(Response(404, b"", {}, FILE))
            with self.assertRaises(CommandError):
                call_command(
                    "audit_jrc_file",
                    "--source",
                    self.channel.slug,
                    "--apply",
                    stdout=io.StringIO(),
                )
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.last_status, "not_tested")
