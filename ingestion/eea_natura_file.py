"""Exact reviewed EEA Natura 2000 table for the K5 large-file trial; on demand only."""

from ingestion.downloads import validated_chunk
from ingestion.models import DownloadJob
from ingestion.transport import FetchError

SLUG = "eea-natura2000-sites-2021-file"
REGISTRY_ID = 176
URL = (
    "https://sdi.eea.europa.eu/datastore/public/"
    "eea_v_3035_100_k_natura2000_p_2021_v12_r01/TABULAR/CSV/"
    "Natura2000_end2021_rev1_NATURA2000SITES.csv"
)
SIZE = 64278449
ETAG = '"41985eaa58ce3525f8939be00536e351"'
# User decision 2026-10-08 (K5): this channel only, 64 MiB; the default stays 50 MiB.
MAX_FILE_BYTES = 64 * 1024 * 1024
CONFIG = {
    "dataset_uuid": "dae737fd-7ee1-4b0a-9eb7-1954eec00c65",
    "release": "end2021 rev1",
    "table": "NATURA2000SITES",
    "max_file_bytes": MAX_FILE_BYTES,
}
ATTRIBUTION = (
    "EEA / DG ENV — Natura 2000 (end2021 rev1), tabela NATURA2000SITES; CC BY 4.0 "
    "według rekordu katalogu EEA dae737fd-7ee1-4b0a-9eb7-1954eec00c65 (sprawdzone "
    "2026-10-08). Obszary wszystkich państw UE, w tym Polski; stan na koniec 2021 r., "
    "nie bieżący rejestr. Pobieranie wyłącznie na żądanie; kopia wygasa po 30 dniach. "
    "https://sdi.eea.europa.eu/catalogue/srv/eng/catalog.search#/metadata/"
    "dae737fd-7ee1-4b0a-9eb7-1954eec00c65"
)


def audit_file(client):
    """One 1 KiB Range request; the reviewed version is pinned by size and strong ETag."""
    response = client.get(URL, headers={"Range": "bytes=0-1023"}, allow_partial=True)
    total, etag = validated_chunk(DownloadJob(offset=0), response)
    if (
        response.url != URL
        or response.status != 206
        or len(response.body) != 1024
        or total != SIZE
        or etag != ETAG
        or b"\x00" in response.body
    ):
        raise FetchError("EEA Natura 2000 file range, size, version or text form changed")
    return {
        "status": "verified",
        "http": 206,
        "total_bytes": total,
        "sample_bytes": 1024,
        "etag": etag,
        "max_file_bytes": MAX_FILE_BYTES,
        "scope": "file_access_only_not_content_validation",
    }
