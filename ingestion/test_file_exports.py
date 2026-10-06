import hashlib
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ingestion.downloads import advance_download, enqueue
from ingestion.file_exports import export_descriptor, present_job
from ingestion.gios_noise_file import CONFIG, SLUG, URL
from ingestion.models import DataChannel, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import Response


class FileExportTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="f" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=48, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug=SLUG,
            name="GIOS historical",
            kind="file",
            region="PL",
            url=URL,
            config=CONFIG.copy(),
            access_approved=True,
        )
        self.staff = get_user_model().objects.create_user("file-reader", is_staff=True)

    def complete(self):
        job, _ = enqueue(self.channel)
        client = Mock()
        body = b"fixture-for-export-headers"
        client.get.return_value = Response(
            206,
            body,
            {"content-range": f"bytes 0-{len(body) - 1}/{len(body)}", "etag": '"v1"'},
            URL,
        )
        advance_download(job, client)
        return job, body

    def test_reviewed_xls_export_preserves_bytes_and_security_headers(self):
        job, body = self.complete()
        self.client.force_login(self.staff)
        response = self.client.get(f"/data/files/{job.pk}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.ms-excel")
        self.assertEqual(
            response["Content-Disposition"], 'attachment; filename="GIOS-halas-drogowy-2024.xls"'
        )
        self.assertEqual(b"".join(response.streaming_content), body)
        self.assertEqual(response["ETag"], '"' + hashlib.sha256(body).hexdigest() + '"')
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_unknown_or_changed_configuration_keeps_binary_fallback(self):
        job, _ = self.complete()
        for field, value in (
            ("slug", "other"),
            ("url", "https://example.com/file.xls"),
            ("config", {**CONFIG, "measurement_year": 2025}),
        ):
            channel = DataChannel.objects.get(pk=self.channel.pk)
            setattr(channel, field, value)
            self.assertEqual(
                export_descriptor(channel, job)["media_type"], "application/octet-stream"
            )
        job.config_hash = "changed"
        self.assertTrue(export_descriptor(self.channel, job)["filename"].endswith(".bin"))

    def test_historical_page_has_no_false_observation_claim_or_remote_fetch(self):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("Remote HTTP")):
            response = self.client.get(f"/data/{self.channel.pk}/")
        self.assertContains(response, "To dane historyczne, nie pomiary z ostatnich 30 dni")
        self.assertContains(response, "GIOS-halas-drogowy-2024.xls")
        self.assertNotContains(response, "Pobranie i podgląd wykluczają obserwacje")
        self.assertNotContains(response, "Nie pobrano jeszcze danych")
        self.assertFalse(DownloadJob.objects.exists())

    def test_expired_copy_has_no_download_or_restart_link(self):
        job, _ = self.complete()
        DownloadJob.objects.filter(pk=job.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.client.force_login(self.staff)
        response = self.client.get(f"/data/{self.channel.pk}/")
        self.assertContains(response, "Kopia wygasła")
        self.assertNotContains(response, f"/data/files/{job.pk}/download/")
        self.assertEqual(self.client.get(f"/data/files/{job.pk}/download/").status_code, 404)

    def test_revoked_approval_hides_actions_and_blocks_export(self):
        job, _ = self.complete()
        DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
        self.client.force_login(self.staff)
        response = self.client.get(f"/data/{self.channel.pk}/")
        self.assertContains(response, "Dostęp niezatwierdzony")
        self.assertNotContains(response, "Pobierz kompletny plik")
        self.assertNotContains(response, "Zleć pobranie w tle")
        self.assertEqual(self.client.get(f"/data/files/{job.pk}/download/").status_code, 404)

    def test_anonymous_and_regular_user_cannot_export_or_see_jobs(self):
        job, _ = self.complete()
        url = f"/data/files/{job.pk}/download/"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertNotContains(self.client.get(f"/data/{self.channel.pk}/"), f"Zlecenie #{job.pk}")
        self.client.force_login(get_user_model().objects.create_user("ordinary"))
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_progress_and_status_labels(self):
        job, _ = enqueue(self.channel)
        job.offset, job.total_bytes = 2, 4
        view = present_job(job, timezone.now(), True)
        self.assertEqual(view.progress_percent, 50)
        self.assertEqual(view.display_status, "W kolejce")
        self.assertFalse(view.download_available)
        job.total_bytes = None
        self.assertIsNone(present_job(job, timezone.now(), True).progress_percent)
