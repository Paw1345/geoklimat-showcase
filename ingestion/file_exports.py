"""Presentation of reviewed file profiles; no MIME guessing from remote headers."""

from ingestion.downloads import configuration

STATUS_LABELS = {
    "queued": "W kolejce",
    "running": "Pobieranie",
    "retry": "Oczekuje na ponowną próbę",
    "complete": "Gotowy",
    "blocked": "Zablokowany",
    "expired": "Wygasły",
    "superseded": "Zastąpiony nową próbą",
}


def export_descriptor(channel, job=None):
    from ingestion.gios_noise_file import CONFIG, SLUG, URL

    reviewed = (
        channel.slug == SLUG
        and channel.url == URL
        and channel.config == CONFIG
        and channel.kind == "file"
        and channel.region == "PL"
    )
    if job is not None:
        reviewed = reviewed and job.url == URL and job.config_hash == configuration(channel)
    if reviewed:
        return {
            "filename": "GIOS-halas-drogowy-2024.xls",
            "media_type": "application/vnd.ms-excel",
            "measurement_year": 2024,
            "scope": "Historyczne pomiary hałasu drogowego za 2024 r., Polska.",
        }
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
