"""Controlled HTTPS evidence; never contacts EEA or changes live source policy."""

import hashlib
import ssl
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from django.test import TestCase
from django.utils import timezone

from ingestion.downloads import enqueue, restart_blocked, run_downloads
from ingestion.models import DataChannel, RegistryEntry, RegistryRelease
from ingestion.transport import Client, FetchError, NoRedirect


class FileHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # No request/credential logging from the controlled test server.

    def do_GET(self):
        if self.path == "/robots.txt":
            rule = "Disallow: /file" if self.server.mode == "robots-disallow" else "Allow: /"
            self.reply(200, f"User-agent: *\n{rule}\n".encode())
            return
        if self.path != "/file":
            self.reply(404, b"")
            return
        request_range = self.headers.get("Range")
        match = self.headers.get("If-Match")
        self.server.file_requests.append((request_range, match))
        mode = self.server.mode
        if mode == "precondition" and match != '"version2"':
            self.reply(412, b"")
        elif mode == "changed" and match not in (None, '"version2"'):
            # Real If-Match evaluation against the current representation (RFC 9110 13.1.1).
            self.reply(412, b"")
        elif mode == "no-range":
            self.reply(200, b"abcdefgh")
        elif mode == "reset":
            self.server.file_statuses.append("reset")
            self.close_connection = True  # Drop the TCP connection without any response.
        elif mode == "unavailable":
            self.reply(503, b"", {"Retry-After": "120"})
        else:
            body, etag, total = b"abcdefgh", '"version1"', 8
            if mode == "changed":
                body, etag = b"abcdWXYZ", '"version2"'
            start = 0 if request_range == "bytes=0-4194303" else 4
            payload = body[start : start + 4]
            if mode == "total-changed":
                total = 9
            headers = {"Content-Range": f"bytes {start}-{start + 3}/{total}"}
            if mode == "weak-etag":
                headers["ETag"] = 'W/"version1"'
            elif mode != "no-etag":
                headers["ETag"] = etag
            if mode == "truncate":
                # Promise four bytes, send two, then close: a cut transfer mid-chunk.
                self.reply(206, payload, headers, sent=payload[:2])
                self.close_connection = True
                return
            self.reply(206, payload, headers)

    def reply(self, status, payload, headers=None, sent=None):
        if self.path == "/file":
            self.server.file_statuses.append(status)
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload if sent is None else sent)


@contextmanager
def https_fixture():
    """Exact loopback test seam; real TLS, robots, urllib and download state machine."""
    with tempfile.TemporaryDirectory(prefix="geoklimat-https-test-") as directory:
        cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=GeoKlimat test fixture",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-addext",
                "subjectAltName=IP:127.0.0.1",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign",
                "-addext",
                "extendedKeyUsage=serverAuth",
            ],
            check=True,
            timeout=10,
            capture_output=True,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        with ThreadingHTTPServer(("127.0.0.1", 0), FileHandler) as server:
            server.daemon_threads = True
            server.socket = context.wrap_socket(server.socket, server_side=True)
            server.mode, server.file_requests = "range", []
            server.file_statuses = []
            server.url = f"https://127.0.0.1:{server.server_port}/file"
            client = Client()
            server.client_context = ssl.create_default_context(cafile=cert)
            client.opener = build_opener(
                ProxyHandler({}), NoRedirect(), HTTPSHandler(context=server.client_context)
            )
            server.client = client

            def exact_test_url(url):
                parsed = urlsplit(url)
                if (
                    url not in (server.url, server.url.removesuffix("file") + "robots.txt")
                    or parsed.username
                    or parsed.password
                ):
                    raise FetchError("Outside exact controlled HTTPS fixture")

            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                # Only routing/construction seams, never HTTP responses or policy decisions.
                with (
                    patch("ingestion.transport.validate_url", side_effect=exact_test_url),
                    patch("ingestion.downloads.Client", return_value=client),
                ):
                    yield server
            finally:
                server.shutdown()
                thread.join(timeout=5)
                if thread.is_alive():
                    raise RuntimeError("Controlled HTTPS server failed to stop")


class DownloadHttpsTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="d" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=176, release=release, master={}, technical={}
        )

    def make_job(self, url):
        channel = DataChannel.objects.create(
            registry=self.entry,
            slug="controlled-https-file",
            name="Controlled HTTPS file",
            url=url,
            kind="file",
            region="EU",
            access_approved=True,
        )
        return enqueue(channel)[0]

    def make_due(self, job):
        job.next_attempt_at = timezone.now()
        job.save(update_fields=["next_attempt_at"])

    def test_https_range_resume_completes_with_same_etag_and_checksum(self):
        with https_fixture() as origin:
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "running")
            job.refresh_from_db()
            expiry = job.expires_at
            self.assertEqual((job.offset, job.chunks.count()), (4, 1))
            self.make_due(job)
            self.assertEqual(run_downloads()[0]["status"], "complete")
            job.refresh_from_db()
            self.assertEqual((job.offset, job.total_bytes, job.chunks.count()), (8, 8, 2))
            self.assertEqual(job.sha256, hashlib.sha256(b"abcdefgh").hexdigest())
            self.assertEqual(job.expires_at, expiry)
            self.assertEqual(origin.file_statuses, [206, 206])
            self.assertEqual(
                origin.file_requests, [("bytes=0-4194303", None), ("bytes=4-4194307", '"version1"')]
            )

    def assert_resume_blocked(self, mode, message):
        with https_fixture() as origin:
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "running")
            job.refresh_from_db()
            before = (job.offset, job.total_bytes, job.etag, job.sha256, job.expires_at)
            chunks = list(job.chunks.values_list("pk", "sha256", "size_bytes", "payload"))
            origin.mode = mode
            self.make_due(job)
            result = run_downloads()[0]
            job.refresh_from_db()
            self.assertEqual(result["status"], "blocked")
            expected = 412 if mode == "precondition" else 200 if mode == "no-range" else 206
            self.assertEqual(origin.file_statuses, [206, expected])
            self.assertIn(message, result["error"])
            self.assertEqual(
                (job.offset, job.total_bytes, job.etag, job.sha256, job.expires_at), before
            )
            self.assertEqual(
                list(job.chunks.values_list("pk", "sha256", "size_bytes", "payload")), chunks
            )
            self.assertEqual(origin.file_requests[-1], ("bytes=4-4194307", '"version1"'))
            request_count = len(origin.file_requests)
            self.make_due(job)
            self.assertEqual(run_downloads(), [])
            self.assertEqual(len(origin.file_requests), request_count)

    def test_https_412_preserves_cursor_and_blocks_without_retry(self):
        self.assert_resume_blocked("precondition", "Unexpected HTTP response")

    def test_https_200_on_resume_never_appends_full_response(self):
        self.assert_resume_blocked("no-range", "Server did not honor Range")

    def test_https_missing_etag_on_resume_preserves_previous_chunk(self):
        self.assert_resume_blocked("no-etag", "Resumable files require a strong ETag")

    def test_https_missing_etag_blocks_before_first_chunk(self):
        with https_fixture() as origin:
            origin.mode = "no-etag"
            job = self.make_job(origin.url)
            expiry = job.expires_at
            self.assertEqual(run_downloads()[0]["status"], "blocked")
            job.refresh_from_db()
            self.assertIn("Resumable files require a strong ETag", job.error)
            self.assertEqual(
                (job.offset, job.total_bytes, job.sha256, job.chunks.count()), (0, None, "", 0)
            )
            self.assertEqual(job.expires_at, expiry)

    def test_https_small_complete_file_without_range_remains_supported(self):
        with https_fixture() as origin:
            origin.mode = "no-range"
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "complete")
            job.refresh_from_db()
            self.assertEqual((job.offset, job.total_bytes, job.chunks.count()), (8, 8, 1))
            self.assertEqual(job.sha256, hashlib.sha256(b"abcdefgh").hexdigest())
            self.assertEqual(origin.file_statuses, [200])

    def test_https_robots_disallow_blocks_before_file_request(self):
        with https_fixture() as origin:
            origin.mode = "robots-disallow"
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "blocked")
            job.refresh_from_db()
            self.assertIn("robots.txt disallows this path", job.error)
            self.assertEqual((job.offset, job.chunks.count()), (0, 0))
            self.assertEqual(origin.file_requests, [])

    def test_https_fixture_does_not_disable_certificate_verification(self):
        with https_fixture() as origin:
            self.assertTrue(origin.client_context.check_hostname)
            self.assertEqual(origin.client_context.verify_mode, ssl.CERT_REQUIRED)
            with self.assertRaisesMessage(
                FetchError, "Network connection or timeout failure"
            ) as error:
                Client().get(origin.url)
            cause = error.exception.__cause__
            self.assertIsInstance(cause, URLError)
            self.assertIsInstance(cause.reason, ssl.SSLCertVerificationError)
            self.assertEqual(origin.file_requests, [])

    def test_fixture_routing_is_exact_and_production_rejects_loopback(self):
        with https_fixture() as origin:
            url = origin.url
            with self.assertRaisesMessage(FetchError, "Outside exact controlled HTTPS fixture"):
                origin.client.get(url + "?extra=1")
            self.assertEqual(origin.file_requests, [])
        with self.assertRaisesMessage(FetchError, "URL outside approved HTTPS sources"):
            Client().get(url)

    def assert_resumes_after(self, mode, statuses, message):
        """Interrupted resume keeps the cursor, retries later and completes from offset 4."""
        with https_fixture() as origin:
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "running")
            job.refresh_from_db()
            before = (job.offset, job.total_bytes, job.etag, job.expires_at)
            chunks = list(job.chunks.values_list("pk", "sha256", "size_bytes"))
            origin.mode = mode
            self.make_due(job)
            with patch("ingestion.transport.time.sleep"):
                result = run_downloads()[0]
            job.refresh_from_db()
            self.assertEqual(result["status"], "retry")
            self.assertIn(message, result["error"])
            self.assertGreaterEqual(job.next_attempt_at, timezone.now())
            self.assertEqual((job.offset, job.total_bytes, job.etag, job.expires_at), before)
            self.assertEqual(list(job.chunks.values_list("pk", "sha256", "size_bytes")), chunks)
            self.assertEqual(run_downloads(), [])  # Not due yet: no request storm.
            origin.mode = "range"
            self.make_due(job)
            self.assertEqual(run_downloads()[0]["status"], "complete")
            job.refresh_from_db()
            self.assertEqual((job.offset, job.total_bytes, job.chunks.count()), (8, 8, 2))
            self.assertEqual(job.sha256, hashlib.sha256(b"abcdefgh").hexdigest())
            self.assertEqual(job.error, "")
            self.assertEqual(origin.file_statuses, [206, *statuses, 206])
            self.assertEqual(origin.file_requests[-1], ("bytes=4-4194307", '"version1"'))
            return origin

    def test_https_connection_reset_mid_download_retries_and_resumes(self):
        self.assert_resumes_after(
            "reset", ["reset", "reset", "reset"], "Network connection or timeout failure"
        )

    def test_https_truncated_chunk_retries_and_resumes_without_partial_bytes(self):
        self.assert_resumes_after(
            "truncate", [206], "Transfer interrupted before the declared length"
        )

    def test_https_503_with_retry_after_defers_and_resumes(self):
        self.assert_resumes_after("unavailable", [503], "Remote service unavailable")

    def test_https_changed_file_412_then_staff_restart_downloads_new_version(self):
        with https_fixture() as origin:
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "running")
            origin.mode = "changed"
            self.make_due(job)
            self.assertEqual(run_downloads()[0]["status"], "blocked")
            job.refresh_from_db()
            self.assertEqual((job.offset, job.etag, job.chunks.count()), (4, '"version1"', 1))
            fresh, created = restart_blocked(job)
            self.assertTrue(created)
            job.refresh_from_db()
            self.assertEqual(job.status, "superseded")
            self.assertEqual(job.chunks.count(), 1)  # Kept until normal expiry.
            self.assertEqual(run_downloads()[0]["status"], "running")
            self.make_due(fresh)
            self.assertEqual(run_downloads()[0]["status"], "complete")
            fresh.refresh_from_db()
            self.assertEqual((fresh.offset, fresh.etag, fresh.chunks.count()), (8, '"version2"', 2))
            self.assertEqual(fresh.sha256, hashlib.sha256(b"abcdWXYZ").hexdigest())
            self.assertEqual(origin.file_statuses, [206, 412, 206, 206])
            self.assertEqual(
                origin.file_requests,
                [
                    ("bytes=0-4194303", None),
                    ("bytes=4-4194307", '"version1"'),
                    ("bytes=0-4194303", None),
                    ("bytes=4-4194307", '"version2"'),
                ],
            )

    def test_https_weak_etag_blocks_before_first_chunk(self):
        with https_fixture() as origin:
            origin.mode = "weak-etag"
            job = self.make_job(origin.url)
            self.assertEqual(run_downloads()[0]["status"], "blocked")
            job.refresh_from_db()
            self.assertIn("Resumable files require a strong ETag", job.error)
            self.assertEqual((job.offset, job.chunks.count()), (0, 0))

    def test_https_total_change_under_same_etag_refuses_to_mix_versions(self):
        self.assert_resume_blocked("total-changed", "Remote file changed")
