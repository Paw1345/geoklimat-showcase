"""Read-only state of file downloads: cursor, chunk continuity and resumability."""

import hashlib

from django.db import connection, transaction
from django.utils import timezone

from ingestion.downloads import INTERRUPTED
from ingestion.models import DownloadJob

TRANSIENT_ERRORS = {"Network connection or timeout failure", INTERRUPTED}


def chunk_state(job, verify):
    """Chunks must start at 0, follow each other without gaps and sum to the cursor."""
    expected, count, problems = 0, 0, []
    digest = hashlib.sha256()
    chunks = job.chunks.order_by("pk")
    rows = chunks.values_list("pk", "size_bytes", "sha256", "summary")
    for pk, size, sha256, summary in rows.iterator(chunk_size=50):
        count += 1
        offset = summary.get("offset") if isinstance(summary, dict) else None
        if offset != expected:
            problems.append(f"chunk {pk} starts at {offset}, expected {expected}")
        if verify:
            payload = bytes(chunks.filter(pk=pk).values_list("payload", flat=True).get())
            if len(payload) != size or hashlib.sha256(payload).hexdigest() != sha256:
                problems.append(f"chunk {pk} payload disagrees with its size or hash")
            digest.update(payload)
        expected += size
    if expected != job.offset:
        problems.append(f"chunks hold {expected} bytes, cursor is {job.offset}")
    result = {"chunks": count, "chunk_bytes": expected, "problems": problems}
    if verify and job.status == "complete":
        result["sha256_matches"] = digest.hexdigest() == job.sha256
    return result


def describe(job, now, verify):
    resumable = (
        job.status in {"queued", "running", "retry"}
        and job.expires_at > now
        and (job.offset == 0 or bool(job.etag))
    )
    return {
        "job": job.pk,
        "channel": job.channel.slug,
        "status": job.status,
        "offset": job.offset,
        "total_bytes": job.total_bytes,
        "percent": job.offset * 100 // job.total_bytes if job.total_bytes else None,
        "etag": job.etag,
        "sha256": job.sha256,
        "error": job.error,
        "transient_error": job.error in TRANSIENT_ERRORS or job.status == "retry",
        "resumable": resumable,
        "next_attempt_at": job.next_attempt_at,
        "expires_at": job.expires_at,
        "expired": job.expires_at <= now,
        **chunk_state(job, verify),
    }


def report(job_ids=None, verify=False):
    """One consistent read-only snapshot; never fetches, retries or repairs a job."""
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        jobs = DownloadJob.objects.select_related("channel").order_by("pk")
        if job_ids:
            jobs = jobs.filter(pk__in=job_ids)
        now = timezone.now()
        return [describe(job, now, verify) for job in jobs]
