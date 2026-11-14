"""Presentation of reviewed file profiles; no MIME guessing from remote headers."""

import hashlib

from ingestion.downloads import CHUNK_BYTES, configuration, file_limit, reviewed_checksum
from ingestion.transport import FetchError

STATUS_LABELS = {
    "queued": "W kolejce",
    "running": "Pobieranie",
    "retry": "Oczekuje na ponowną próbę",
    "complete": "Gotowy",
    "blocked": "Zablokowany",
    "expired": "Wygasły",
    "superseded": "Zastąpiony nową próbą",
}


def write_verified_file(job, now, stream):
    """Copy validated chunks to a bounded temporary stream before sending HTTP headers."""
    channel = job.channel
    total = job.total_bytes
    expected_checksum = reviewed_checksum(channel)
    if (
        job.status != "complete"
        or job.expires_at <= now
        or not channel.access_approved
        or channel.kind != "file"
        or job.url != channel.url
        or job.config_hash != configuration(channel)
        or type(total) is not int
        or not 0 < total <= file_limit(channel)
        or job.offset != total
        or (expected_checksum and job.sha256 != expected_checksum)
    ):
        raise FetchError("File export unavailable: state, approval or limit changed")
    digest, written = hashlib.sha256(), 0
    for chunk in job.chunks.order_by("pk").iterator(chunk_size=1):
        payload = bytes(chunk.payload)
        receipt = chunk.summary
        if (
            chunk.channel_id != channel.pk
            or chunk.expires_at <= now
            or not 0 < len(payload) <= CHUNK_BYTES
            or len(payload) != chunk.size_bytes
            or hashlib.sha256(payload).hexdigest() != chunk.sha256
            or not isinstance(receipt, dict)
            or receipt.get("kind") != "file_chunk"
            or receipt.get("scope") != channel.region
            or type(receipt.get("offset")) is not int
            or receipt["offset"] != written
            or written + len(payload) > total
        ):
            raise FetchError("File export chunk integrity failed")
        stream.write(payload)
        digest.update(payload)
        written += len(payload)
    if written != total or digest.hexdigest() != job.sha256:
        raise FetchError("File export size or checksum failed")
    return digest.hexdigest()


def reviewed_profiles():
    from ingestion import eea_natura_file, gios_noise_file, gios_prtr_file

    return [
        (
            gios_prtr_file,
            "PL",
            {
                "filename": "GIOS-E-PRTR-PL-2017.xml",
                "media_type": "application/xml",
                "scope": (
                    "E-PRTR Polska za 2017 r.; oryginalny XML historyczny. "
                    "Uwolnienia/transfery zachowane bez interpretacji kodów i jednostek; "
                    "nie są to bieżące emisje ani pomiary z ostatnich 30 dni."
                ),
            },
        ),
        (
            gios_noise_file,
            "PL",
            {
                "filename": "GIOS-halas-drogowy-2024.xls",
                "media_type": "application/vnd.ms-excel",
                "measurement_year": 2024,
                "scope": "Historyczne pomiary hałasu drogowego za 2024 r., Polska.",
            },
        ),
        (
            eea_natura_file,
            "EU",
            {
                "filename": "Natura2000_end2021_rev1_NATURA2000SITES.csv",
                "media_type": "text/csv; charset=utf-8",
                "scope": "Obszary Natura 2000 w UE (w tym Polska), stan na koniec 2021 r.",
            },
        ),
    ]


def export_descriptor(channel, job=None):
    for module, region, descriptor in reviewed_profiles():
        reviewed = (
            channel.slug == module.SLUG
            and channel.url == module.URL
            and channel.config == module.CONFIG
            and channel.kind == "file"
            and channel.region == region
        )
        if job is not None:
            reviewed = (
                reviewed and job.url == module.URL and job.config_hash == configuration(channel)
            )
        if reviewed:
            return descriptor
    return {
        "filename": f"geoklimat-file-{job.pk}.bin" if job else "plik.bin",
        "media_type": "application/octet-stream",
        "scope": (
            "Zakres opisany przez dostawcę w atrybucji kanału; "
            "nie został wywnioskowany z nazwy pliku."
        ),
    }


def present_job(job, now, approved):
    job.display_status = STATUS_LABELS.get(job.status, job.status)
    job.copy_expired = job.expires_at <= now
    if job.copy_expired:
        job.display_status = "Wygasły"
    job.download_available = job.status == "complete" and not job.copy_expired and approved
    job.restart_available = job.status == "blocked" and not job.copy_expired and approved
    job.progress_percent = (
        min(100, max(0, job.offset * 100 // job.total_bytes)) if job.total_bytes else None
    )
    return job
