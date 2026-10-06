"""Public ECMWF STAC catalogues verified 2026-09-19; no authenticated retrieval."""

PROFILES = {
    193: "https://cds.climate.copernicus.eu/api/catalogue/v1/collections",
    194: "https://ads.atmosphere.copernicus.eu/api/catalogue/v1/collections",
}
CONFIG = {"verify_total": True}
NOTE = (
    "Publiczny katalog kolekcji STAC i monitoring zmian metadanych. "
    "Pobieranie właściwych danych nadal oczekuje na uprawnienia (zaakceptowany wyjątek)."
)
