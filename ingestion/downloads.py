"""On-demand, bounded Range GET downloads. Chunks share the data-cache quota."""

import hashlib
import json
import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ingestion.data_service import store_snapshot
from ingestion.models import DataChannel, DownloadJob
from ingestion.service import batch_lock
from ingestion.transport import Client, FetchError

CHUNK_BYTES = 4 * 1024 * 1024


def configuration(channel):
    return hashlib.sha256(
        json.dumps(
            [channel.url, channel.kind, channel.region, channel.config], sort_keys=True
        ).encode()
    ).hexdigest()


def enqueue(channel):
    with transaction.atomic():
        channel = DataChannel.objects.select_for_update().get(pk=channel.pk)
        if (
            channel.kind != "file"
            or not channel.access_approved
            or channel.region not in {"PL", "EU"}
        ):
            raise FetchError("An approved PL/EU file channel is required")
        fingerprint = configuration(channel)
        existing = (
            channel.downloads.filter(config_hash=fingerprint, expires_at__gt=timezone.now())
            .exclude(status__in=["expired", "superseded"])
            .first()
        )
        if existing:
            return existing, False
        job = DownloadJob.objects.create(
            channel=channel,
            url=channel.url,
            config_hash=fingerprint,
            expires_at=timezone.now() + timedelta(days=min(30, settings.DATA_RETENTION_DAYS)),
        )
        return job, True


def restart_blocked(job):
    """Explicit staff request; keep the superseded chunks until normal expiry."""
    with transaction.atomic():
        channel = DataChannel.objects.select_for_update().get(pk=job.channel_id)
        job = DownloadJob.objects.select_for_update().get(pk=job.pk)
        if job.status != "blocked":
            return job, False
        if not channel.access_approved:
            raise FetchError("File channel approval revoked")
        job.status = "superseded"
        job.save(update_fields=["status"])
        return enqueue(channel)


def validated_chunk(job, response):
    headers = response.headers
    payload = response.body
    if headers.get("content-encoding", "identity") != "identity":
        raise FetchError("Encoded file chunks are unsupported")
    if len(payload) > CHUNK_BYTES:
        raise FetchError("File chunk exceeds memory budget")
    if response.status == 200 and job.offset == 0:
        # A server without Range support is usable only for a complete small file.
        if headers.get("content-length") and int(headers["content-length"]) != len(payload):
            raise FetchError("Incomplete full-file response")
        return len(payload), ""
    if response.status != 206:
        raise FetchError("Server did not honor Range; refusing to append")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers.get("content-range", ""))
    if not match:
        raise FetchError("Missing or invalid Content-Range")
    start, end, total = map(int, match.groups())
    if start != job.offset or end < start or end >= total or end - start + 1 != len(payload):
        raise FetchError("File range offset or length mismatch")
    etag = headers.get("etag", "")
    if total > len(payload) and (
        not re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', etag) or len(etag) > 500
    ):
        raise FetchError("Resumable files require a strong ETag")
    if job.offset and (etag != job.etag or total != job.total_bytes):
        raise FetchError("Remote file changed; refusing to mix versions")
    return total, etag


def advance_download(job, client):
    channel = job.channel
    if job.expires_at <= timezone.now():
        job.status = "expired"
        job.save(update_fields=["status"])
        return
    if (
        not channel.access_approved
        or channel.kind != "file"
        or configuration(channel) != job.config_hash
    ):
        raise FetchError("File channel approval revoked or configuration changed")
    client.max_bytes = CHUNK_BYTES
    headers = {"Range": f"bytes={job.offset}-{job.offset + CHUNK_BYTES - 1}"}
    if job.offset:
        headers["If-Match"] = job.etag
    response = client.get(job.url, headers, allow_partial=True)
    total, etag = validated_chunk(job, response)
    if total > settings.DATA_STORAGE_MAX_BYTES:
        raise FetchError("File exceeds the entire data quota")
    with transaction.atomic():
        channel = DataChannel.objects.select_for_update().get(pk=channel.pk)
        if not channel.access_approved or configuration(channel) != job.config_hash:
            raise FetchError("File approval or configuration changed during transfer")
        if job.expires_at <= timezone.now():
            raise FetchError("Download expired during transfer")
        snapshot, _ = store_snapshot(
            channel,
            response.body,
            "application/octet-stream",
            {"kind": "file_chunk", "scope": channel.region, "offset": job.offset},
            deduplicate=False,
        )
        snapshot.download_job = job
        snapshot.expires_at = job.expires_at
        snapshot.save(update_fields=["download_job", "expires_at"])
        job.offset += len(response.body)
        job.total_bytes, job.etag, job.error = total, etag, ""
        job.status = "complete" if job.offset == total else "running"
        job.next_attempt_at = timezone.now() + timedelta(seconds=30)
        if job.status == "complete":
            checksum = hashlib.sha256()
            for chunk in job.chunks.order_by("pk").iterator(chunk_size=1):
                checksum.update(bytes(chunk.payload))
            job.sha256 = checksum.hexdigest()
        job.save()


def run_downloads():
    results = []
    with batch_lock() as acquired:
        if not acquired:
            return [{"status": "busy"}]
        jobs = (
            DownloadJob.objects.filter(
                status__in=["queued", "running", "retry"], next_attempt_at__lte=timezone.now()
            )
            .select_related("channel")
            .order_by("created_at")[:5]
        )
        client = Client()
        for job in jobs:
            try:
                advance_download(job, client)
            except Exception as exc:
                job.refresh_from_db()
                job.error = str(exc)[:300] if isinstance(exc, FetchError) else type(exc).__name__
                transient = isinstance(exc, FetchError) and (
                    exc.status in {429, 500, 502, 503, 504}
                    or job.error == "Network connection or timeout failure"
                    or job.error.startswith("Data quota reached")
                )
                job.status = "retry" if transient else "blocked"
                job.next_attempt_at = timezone.now() + timedelta(
                    seconds=max(60, getattr(exc, "retry_after", 0))
                )
                job.save(update_fields=["error", "status", "next_attempt_at"])
            results.append(
                {
                    "job": job.pk,
                    "status": job.status,
                    "bytes": job.offset,
                    "total": job.total_bytes,
                    "error": job.error,
                }
            )
    return results
