"""Explicit public routes reviewed 2026-09-18; never modify workbook evidence."""

PROFILES = {
    10: (
        "https://www.geoportal.gov.pl/pl/aktualnosci/",
        {"listing": "geoportal_news"},
        "Aktualności Geoportalu: bieżąca strona listy, bez pełnego archiwum.",
    ),
    11: (
        "https://www.geoportal.gov.pl/pl/usluga/",
        {"listing": "geoportal_services"},
        "Katalog opisów usług Geoportalu; nie pobranie danych wszystkich usług.",
    ),
    17: (
        "https://www.ekoportal.gov.pl/",
        {"listing": "ekoportal"},
        "Publiczne aktualności Ekoportalu: okno strony głównej, nie baza PDWD.",
    ),
    28: (
        "https://www.kobize.pl/",
        {"listing": "kobize"},
        "Publiczne aktualności KOBiZE: okno strony głównej, bez danych kont i raportów.",
    ),
    31: (
        "https://www.gov.pl/web/nfosigw/harmonogram-naborow",
        {"listing": "nfosigw_calls"},
        "Wiersze harmonogramu naborów; terminy jako tekst dostawcy, bez danych koordynatorów.",
    ),
    179: (
        "https://knowledge-base.inspire.ec.europa.eu/tools/document-library_en",
        {"listing": "inspire_documents", "pagination": "ecl_next", "max_pages": 20},
        "Biblioteka dokumentów INSPIRE: wszystkie strony do limitu 20; metadane, bez plików.",
    ),
}

# Only these untouched initial routes may be upgraded automatically.
PREVIOUS_URLS = {
    17: "https://www.ekoportal.gov.pl/",
    28: "https://www.kobize.pl/",
    31: "https://www.gov.pl/web/nfosigw/nabory",
    179: "https://knowledge-base.inspire.ec.europa.eu/",
}
