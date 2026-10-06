import hashlib
import io
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from ingestion.downloads import (
    advance_download,
    enqueue,
    restart_blocked,
    run_downloads,
    validated_chunk,
)
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response


class DownloadTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="c" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=53, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="file",
            name="File",
            kind="file",
            region="PL",
            url="https://www.geoportal.gov.pl/file.zip",
            access_approved=True,
        )

    def http(self, start=0, body=b"abcd", total=8, etag='"version1"'):
        return Response(
            206,
            body,
            {"content-range": f"bytes {start}-{start + len(body) - 1}/{total}", "etag": etag},
            self.channel.url,
        )

    def test_queue_idempotent_and_dry_run(self):
        call_command("queue_download", source="file", stdout=io.StringIO())
        self.assertFalse(DownloadJob.objects.exists())
        job, created = enqueue(self.channel)
        same, again = enqueue(self.channel)
        self.assertTrue(created)
        self.assertFalse(again)
        self.assertEqual(job.pk, same.pk)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_file_larger_than_legacy_50_mib_limit(self):
        from ingestion.downloads import CHUNK_BYTES

        job, _ = enqueue(self.channel)
        block = b"x" * CHUNK_BYTES
        total = CHUNK_BYTES * 14  # 56 MiB; bounded chunks, never one large buffer.
        transport = Mock()
        expected = hashlib.sha256()
        for offset in range(0, total, CHUNK_BYTES):
            transport.get.return_value = self.http(offset, block, total)
            job = DownloadJob.objects.get(pk=job.pk)
            advance_download(job, transport)
            expected.update(block)
        self.assertEqual((job.status, job.offset, job.chunks.count()), ("complete", total, 14))
        self.assertEqual(job.sha256, expected.hexdigest())
        self.assertEqual(transport.max_bytes, CHUNK_BYTES)
        staff = get_user_model().objects.create_user("large-file-reader", is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(f"/data/files/{job.pk}/download/")
        actual, streamed = hashlib.sha256(), 0
        for piece in response.streaming_content:
            self.assertLessEqual(len(piece), CHUNK_BYTES)
            actual.update(piece)
            streamed += len(piece)
        self.assertEqual(streamed, total)
        self.assertEqual(actual.hexdigest(), expected.hexdigest())

    def test_unapproved_or_non_file_rejected(self):
        self.channel.access_approved = False
        self.channel.save()
        with self.assertRaises(FetchError):
            enqueue(self.channel)

    def test_queue_approval_race_returns_bad_request(self):
        user = get_user_model().objects.create_user("approval-race", is_staff=True)
        self.client.force_login(user)
        with patch("core.data_views.enqueue", side_effect=FetchError("Approval revoked")):
            response = self.client.post(f"/data/{self.channel.pk}/request/")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DownloadJob.objects.exists())

    def test_private_chunks_not_counted_as_public_copies(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http()
        advance_download(job, transport)
        response = self.client.get("/data/")
        self.assertEqual(response.context["snapshots"], 0)
        self.assertEqual(response.context["used"], 4)

    def test_explicit_restart_preserves_old_chunks_and_is_idempotent(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http()
        advance_download(job, transport)
        job.status = "blocked"
        job.save(update_fields=["status"])
        fresh, created = restart_blocked(job)
        self.assertTrue(created)
        self.assertNotEqual(fresh.pk, job.pk)
        self.assertEqual(fresh.offset, 0)
        self.assertEqual(job.chunks.count(), 1)
        _, created = restart_blocked(job)
        self.assertFalse(created)
        self.assertEqual(DownloadJob.objects.count(), 2)

    def test_transport_partial_responses_are_opt_in(self):
        from ingestion.transport import Client

        for allow in (False, True):
            transport = Client()
            with patch.object(
                transport,
                "_request",
                side_effect=[Response(404, b"", {}, self.channel.url), self.http()],
            ):
                if allow:
                    self.assertEqual(
                        transport.get(self.channel.url, allow_partial=True).status, 206
                    )
                else:
                    with self.assertRaises(FetchError):
                        transport.get(self.channel.url)

    def test_resume_checksum_and_stream_complete_only(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http()
        advance_download(job, transport)
        self.assertEqual(job.offset, 4)
        self.assertEqual(job.status, "running")
        staff = get_user_model().objects.create_user("reader", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(f"/data/files/{job.pk}/download/").status_code, 404)
        part = job.chunks.get()
        self.assertEqual(self.client.get(f"/data/snapshots/{part.pk}/download/").status_code, 404)
        transport.get.return_value = self.http(4, b"efgh")
        job = DownloadJob.objects.get(pk=job.pk)
        advance_download(job, transport)
        self.assertEqual(job.sha256, hashlib.sha256(b"abcdefgh").hexdigest())
        self.assertEqual(transport.get.call_args.args[1]["If-Match"], '"version1"')
        reply = self.client.get(f"/data/files/{job.pk}/download/")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(b"".join(reply.streaming_content), b"abcdefgh")
        self.assertEqual(reply["Content-Length"], "8")
        self.client.logout()
        self.assertEqual(self.client.get(f"/data/files/{job.pk}/download/").status_code, 302)

    def test_changed_etag_or_size_never_appended(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http()
        advance_download(job, transport)
        for reply in [self.http(4, b"efgh", etag='"new"'), self.http(4, b"efgh", total=9)]:
            transport.get.return_value = reply
            with self.assertRaises(FetchError):
                advance_download(job, transport)
        job.refresh_from_db()
        self.assertEqual((job.offset, job.chunks.count()), (4, 1))

    def test_range_validation_and_weak_etag(self):
        job, _ = enqueue(self.channel)
        for reply in [
            self.http(1),
            self.http(etag='W/"weak"'),
            Response(
                206, b"abcd", {"content-range": "bytes 0-7/8", "etag": '"v"'}, self.channel.url
            ),
            Response(206, b"abcd", {}, self.channel.url),
        ]:
            with self.assertRaises(FetchError):
                validated_chunk(job, reply)
        job.offset = 4
        with self.assertRaises(FetchError):
            validated_chunk(job, Response(200, b"whole", {}, job.url))

    def test_small_server_without_ranges(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = Response(200, b"small", {"content-length": "5"}, job.url)
        advance_download(job, transport)
        self.assertEqual(job.status, "complete")
        self.assertEqual(job.total_bytes, 5)

    def test_shared_quota_and_rollback_cursor(self):
        job, _ = enqueue(self.channel)
        DataSnapshot.objects.create(
            channel=self.channel,
            payload=b"old",
            size_bytes=3,
            sha256="0" * 64,
            expires_at=timezone.now() + timedelta(days=1),
            media_type="application/octet-stream",
        )
        transport = Mock()
        transport.get.return_value = self.http(total=6)
        with override_settings(DATA_STORAGE_MAX_BYTES=6), self.assertRaises(FetchError):
            advance_download(job, transport)
        job.refresh_from_db()
        self.assertEqual(job.offset, 0)
        self.assertEqual(job.chunks.count(), 0)
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_fifteen_gb_file_can_start_and_quota_remains_retryable(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http(total=15_000_000_000)
        advance_download(job, transport)
        self.assertEqual(job.offset, 4)
        self.assertEqual(job.status, "running")
        self.assertEqual(job.total_bytes, 15_000_000_000)
        job.next_attempt_at = timezone.now()
        job.save(update_fields=["next_attempt_at"])
        transport.get.return_value = self.http(start=4, total=15_000_000_000)
        with (
            patch("ingestion.downloads.Client", return_value=transport),
            patch(
                "ingestion.downloads.store_snapshot",
                side_effect=FetchError("Data quota reached; prune expired data or reduce scope"),
            ),
        ):
            result = run_downloads()
        self.assertEqual(result[0]["status"], "retry")
        job.refresh_from_db()
        self.assertEqual(job.offset, 4)
        self.assertEqual(job.chunks.count(), 1)

    def test_file_over_entire_quota_rejected(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http(total=15_000_000_001)
        with self.assertRaises(FetchError):
            advance_download(job, transport)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_job_save_failure_rolls_back_chunk(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http()
        with (
            patch.object(DownloadJob, "save", side_effect=RuntimeError("crash")),
            self.assertRaises(RuntimeError),
        ):
            advance_download(job, transport)
        job.refresh_from_db()
        self.assertEqual(job.offset, 0)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_approval_revoked_during_request(self):
        job, _ = enqueue(self.channel)
        transport = Mock()

        def fetch(*args, **kwargs):
            DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
            return self.http()

        transport.get.side_effect = fetch
        with self.assertRaises(FetchError):
            advance_download(job, transport)
        self.assertFalse(DataSnapshot.objects.exists())

    def test_transient_retry_keeps_cursor_and_obeys_retry_after(self):
        job, _ = enqueue(self.channel)
        with patch("ingestion.downloads.Client") as transport:
            transport.return_value.get.side_effect = FetchError(
                "Remote service unavailable", 429, 3600
            )
            result = run_downloads()
        self.assertEqual(result[0]["status"], "retry")
        job.refresh_from_db()
        self.assertGreater(job.next_attempt_at, timezone.now() + timedelta(minutes=59))
        self.assertEqual(job.offset, 0)

    def test_expiry_prunes_only_download_payload(self):
        job, _ = enqueue(self.channel)
        transport = Mock()
        transport.get.return_value = self.http()
        advance_download(job, transport)
        expired = timezone.now() - timedelta(seconds=1)
        DownloadJob.objects.filter(pk=job.pk).update(expires_at=expired)
        job.chunks.update(expires_at=expired)
        call_command("prune_data", apply=True, stdout=io.StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, "expired")
        self.assertFalse(job.chunks.exists())
        self.assertTrue(DataChannel.objects.filter(pk=self.channel.pk).exists())

    def test_staff_post_enqueues_without_http_and_csrf_enforced(self):
        from django.test import Client

        user = get_user_model().objects.create_user("operator", is_staff=True)
        url = f"/data/{self.channel.pk}/request/"
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 405)
        with patch("ingestion.downloads.Client") as transport:
            self.assertEqual(self.client.post(url).status_code, 302)
            transport.assert_not_called()
        self.assertEqual(DownloadJob.objects.count(), 1)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(user)
        self.assertEqual(strict.post(url).status_code, 403)
