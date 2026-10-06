"""Exact reviewed historical file; never an automatic current-measurement feed."""

import json

from ingestion.downloads import validated_chunk
from ingestion.models import DownloadJob
from ingestion.transport import FetchError

SLUG = "gios-noise-road-2024-file"
URL = "https://api.dane.gov.pl/media/resources/20251229/Wyniki_pomiarow_halasu_drogowego_w_2024_roku.xls"
SIZE = 754688
CONFIG = {"dataset_id": 1404, "resource_id": 989426, "measurement_year": 2024}
ATTRIBUTION = (
    "GIOŚ, Państwowy Monitoring Środowiska — wyniki hałasu drogowego za2024r.; "
    "zbiór1404/zasób989426 w dane.gov.pl, CC BY4.0 według metadanych katalogu. "
    "Dane historyczne, nie pomiary z ostatnich30dni. Pobieranie wyłącznie na żądanie; "
    "lokalna kopia wygasa po30dniach od zlecenia. Nie zastępuje nowego API hałasu. "
    "https://dane.gov.pl/pl/dataset/1404"
)


def audit_file(client):
    records = []
    for path, expected_type, expected_id in (
        ("datasets/1404", "dataset", "1404"),
        ("resources/989426", "resource", "989426"),
    ):
        url = "https://api.dane.gov.pl/1.4/" + path
        response = client.get(url)
        record = json.loads(response.body)["data"]
        if response.url != url or (record.get("type"), record.get("id")) != (
            expected_type,
            expected_id,
        ):
            raise FetchError("GIOS file catalogue identity changed")
        records.append(record)
    dataset, resource = records
    da, ra = dataset["attributes"], resource["attributes"]
    if (
        dataset["relationships"]["institution"]["data"] != {"type": "institution", "id": "100"}
        or resource["relationships"]["dataset"]["data"] != {"type": "dataset", "id": "1404"}
        or da.get("license_name") != "CC BY 4.0"
        or not da.get("license_condition_default_cc40")
        or any(
            da.get(key)
            for key in (
                "license_condition_custom_description",
                "license_condition_db_or_copyrighted",
                "license_condition_personal_data",
                "license_condition_original",
            )
        )
        or ra.get("link") != URL
        or ra.get("file_size") != SIZE
        or ra.get("format") != "xls"
        or ra.get("title") != "Wyniki pomiarów hałasu drogowego w 2024 roku"
        or ra.get("contains_protected_data") is not False
    ):
        raise FetchError("GIOS file provenance, licence or reviewed version changed")
    response = client.get(URL, headers={"Range": "bytes=0-1023"}, allow_partial=True)
    total, etag = validated_chunk(DownloadJob(offset=0), response)
    if (
        response.url != URL
        or response.status != 206
        or len(response.body) != 1024
        or total != SIZE
        or response.body[:8] != bytes.fromhex("d0cf11e0a1b11ae1")
    ):
        raise FetchError("GIOS file range, size or container signature changed")
    return {
        "status": "verified",
        "http": 206,
        "total_bytes": total,
        "sample_bytes": 1024,
        "etag": etag,
        "measurement_year": 2024,
        "scope": "file_access_only_not_measurement_validation",
    }
