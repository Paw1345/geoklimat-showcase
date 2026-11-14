"""Pinned public E-PRTR 2016 original, read only by the annual releases table."""

from ingestion.gios_prtr_file import audit_original

RESOURCE_ID = 19735
URL = "https://api.dane.gov.pl/media/resources/20190930/E-PRTR_data_PL_2016.xml"
SIZE = 3952050
SHA256 = "4d9f4adb7eee156b9459745fe2ffc9d736c3539c31073a1689c78e8ef090c4c1"
YEAR = 2016


def audit_file(client):
    return audit_original(client, RESOURCE_ID, URL, SIZE, SHA256, YEAR)
