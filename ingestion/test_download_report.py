import hashlib
import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ingestion.data_service import store_snapshot
from ingestion.download_report import report
from ingestion.downloads import INTERRUPTED, enqueue
from ingestion.models import DataChannel, DownloadJob, RegistryEntry, RegistryRelease


class DownloadReportTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="e" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=176, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            slug="report-file",
            name="Report file",
            kind="file",
            region="EU",
            url="https://sdi.eea.europa.eu/datastore/public/file.csv",
            access_approved=True,
        )
        self.job = enqueue(self.channel)[0]

    def add_chunk(self, payload, offset):
        snapshot, _ = store_snapshot(
            self.channel,
            payload,
            "application/octet-stream",
            {"kind": "file_chunk", "scope": "EU", "offset": offset},
            deduplicate=False,
        )
        snapshot.download_job = self.job
        snapshot.save(update_fields=["download_job"])
        return snapshot

    def save_job(self, **fields):
        for name, value in fields.items():
            setattr(self.job, name, value)
        self.job.save()

    def test_interrupted_job_is_resumable_and_chunks_are_contiguous(self):
        self.add_chunk(b"abcd", 0)
        self.save_job(status="retry", offset=4, total_bytes=8, etag='"version1"', error=INTERRUPTED)
        (row,) = report()
        self.assertEqual((row["status"], row["offset"], row["percent"]), ("retry", 4, 50))
        self.assertTrue(row["resumable"])
        self.assertTrue(row["transient_error"])
        self.assertEqual((row["chunks"], row["chunk_bytes"], row["problems"]), (1, 4, []))

    def test_gap_cursor_mismatch_and_tampered_payload_are_reported(self):
        self.add_chunk(b"abcd", 0)
        tampered = self.add_chunk(b"efgh", 5)
        DownloadJob.objects.filter(pk=self.job.pk).update(
            status="complete",
            offset=9,
            total_bytes=9,
            etag='"v"',
            sha256=hashlib.sha256(b"abcdefgh").hexdigest(),
        )
        type(tampered).objects.filter(pk=tampered.pk).update(payload=b"efgX")
        (row,) = report(verify=True)
        self.assertFalse(row["resumable"])
        self.assertEqual(
            row["problems"],
            [
                f"chunk {tampered.pk} starts at 5, expected 4",
                f"chunk {tampered.pk} payload disagrees with its size or hash",
                "chunks hold 8 bytes, cursor is 9",
            ],
        )
        self.assertFalse(row["sha256_matches"])

    def test_complete_verified_job_matches_checksum(self):
        self.add_chunk(b"abcd", 0)
        self.add_chunk(b"efgh", 4)
        self.save_job(
            status="complete",
            offset=8,
            total_bytes=8,
            etag='"v"',
            sha256=hashlib.sha256(b"abcdefgh").hexdigest(),
        )
        (row,) = report(verify=True)
        self.assertEqual(row["problems"], [])
        self.assertTrue(row["sha256_matches"])
        self.assertNotIn("sha256_matches", report()[0])  # Payloads only read on --verify.

    def test_expired_or_blocked_job_is_not_resumable(self):
        self.save_job(status="blocked", error="Remote file changed; refusing to mix versions")
        self.assertFalse(report()[0]["resumable"])
        self.assertFalse(report()[0]["transient_error"])
        self.save_job(status="running", expires_at=timezone.now() - timedelta(seconds=1))
        row = report()[0]
        self.assertTrue(row["expired"])
        self.assertFalse(row["resumable"])

    def test_command_filters_jobs_and_writes_nothing(self):
        other = DownloadJob.objects.create(
            channel=self.channel,
            url=self.channel.url,
            config_hash="0" * 64,
            expires_at=timezone.now() + timedelta(days=1),
        )
        before = list(DownloadJob.objects.order_by("pk").values())
        out = StringIO()
        call_command("download_report", "--job", str(other.pk), stdout=out)
        self.assertEqual([row["job"] for row in json.loads(out.getvalue())], [other.pk])
        self.assertEqual(list(DownloadJob.objects.order_by("pk").values()), before)

    def test_report_runs_in_a_read_only_snapshot(self):
        with CaptureQueriesContext(connection) as queries:
            report(verify=True)
        statements = [query["sql"] for query in queries.captured_queries]
        read_only = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        self.assertEqual(statements[1], read_only)  # Right after BEGIN, before any read.
        self.assertFalse(
            [sql for sql in statements if sql.split()[0] in {"INSERT", "UPDATE", "DELETE"}]
        )
