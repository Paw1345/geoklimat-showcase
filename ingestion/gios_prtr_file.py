"""Pinned public E-PRTR 2017 original; no XML interpretation or automatic feed."""

from ingestion.downloads import validated_chunk
from ingestion.gios_major_accidents import API, check_dataset, get_json
from ingestion.models import DownloadJob
from ingestion.transport import FetchError

SLUG = "gios-prtr-2017-file"
URL = "https://api.dane.gov.pl/media/resources/20190930/E-PRTR_data_PL_2017.xml"
SIZE = 3964096
SHA256 = "3eebd25ebd5b06fe30b91f8847d3b886aca1f853f2813bf059430fe4d9e49519"
CONFIG = {
    "dataset_id": 425,
    "resource_id": 19734,
    "reporting_year": 2017,
    "max_file_bytes": SIZE,
    "expected_sha256": SHA256,
}
ATTRIBUTION = (
    "Główny Inspektor Ochrony Środowiska — E-PRTR 2017, Polska; "
    "zbiór425/zasób19734 w dane.gov.pl, CC BY4.0 według metadanych katalogu "
    "(bez dodatkowych warunków). Oryginalny XML bez przetwarzania. Dane historyczne "
    "za2017r., nie bieżące emisje. Rok publikacji pliku nie jest rokiem obserwacji. "
    "Pobieranie na żądanie; lokalna kopia wygasa po30dniach od zlecenia. "
    "Nie potwierdzono jednostek/znaczenia kodów; aplikacja nie interpretuje wartości. "
    "Nie zastępuje nowego API ani pełnego zakresu PRTR. https://dane.gov.pl/pl/dataset/425"
)


def audit_file(client):
    return audit_original(client, 19734, URL, SIZE, SHA256, 2017)


def audit_original(client, resource_id, url, size, sha256, year):
    """Shared reviewed-prefix audit for one pinned annual E-PRTR original."""
    check_dataset(get_json(client, API + "datasets/425", "dataset", 425))
    resource = get_json(client, API + f"resources/{resource_id}", "resource", resource_id)
    attrs = resource["attributes"]
    if (
        resource["relationships"]["dataset"]["data"] != {"type": "dataset", "id": "425"}
        or attrs.get("link") != url
        or attrs.get("file_size") != size
        or attrs.get("format") != "xml"
        or attrs.get("title") != f"E-PRTR {year}"
        or attrs.get("contains_protected_data") is not False
    ):
        raise FetchError("PRTR resource provenance or reviewed version changed")
    response = client.get(url, headers={"Range": "bytes=0-1023"}, allow_partial=True)
    total, etag = validated_chunk(DownloadJob(offset=0), response)
    # Only inspect the reviewed prefix. Do not parse XML or fetch its schema/URLs.
    if (
        response.url != url
        or response.status != 206
        or len(response.body) != 1024
        or total != size
        or not response.body.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")
        or b'xmlns:a="urn:eu:com:env:prtr:data:standard:2"' not in response.body
        or f"<a:ReportingYear>{year}</a:ReportingYear>".encode() not in response.body
        or b"<a:CountryID>PL</a:CountryID>" not in response.body
    ):
        raise FetchError("PRTR range, size or XML prefix changed")
    return {
        "status": "verified",
        "http": 206,
        "total_bytes": total,
        "sample_bytes": 1024,
        "etag": etag,
        "reporting_year": year,
        "expected_sha256": sha256,
        "scope": "file_access_only_not_measurement_validation",
    }
