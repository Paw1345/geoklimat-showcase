"""Workbook taxonomy, retained alongside legacy tags for backwards compatibility."""

MASTER_TOPICS = {
    "spatial_information": ("INSPIRE i IIP", ["INSPIRE", "IIP", "metadata", "interoperability"]),
    "geoportal_geodesy": (
        "Geoportal, geodezja, kartografia",
        ["WMS", "WMTS", "WFS", "ATOM", "GUGiK"],
    ),
    "open_data_api": ("Otwarte dane i API", ["API", "schema", "metadata", "open data"]),
    "climate_adaptation": ("Klimat i adaptacja", ["climate", "adaptation", "resilience"]),
    "emissions_ets_cbam": ("Emisje / ETS / CBAM", ["ETS", "CBAM", "emissions", "KOBiZE"]),
    "air_quality": ("Jakość powietrza", ["PM10", "PM2.5", "NO2", "O3"]),
    "water": ("Wody", ["rivers", "groundwater", "flood", "drought"]),
    "waste_circular": ("Odpady / GOZ", ["BDO", "waste", "recycling"]),
    "nature_biodiversity": ("Przyroda / bioróżnorodność", ["Natura 2000", "species", "habitats"]),
    "soil_geology": ("Gleba / geologia", ["soil", "geology", "minerals", "landslides"]),
    "chemicals_pollution": ("Chemikalia / zanieczyszczenia", ["ECHA", "REACH", "PRTR"]),
    "energy": ("OZE / energia", ["renewables", "efficiency", "offshore"]),
    "environmental_procedures": (
        "OOŚ / SOOŚ / pozwolenia",
        ["EIA", "SEA", "permits", "inspections"],
    ),
    "funding": ("Finansowanie", ["NFOŚ", "WFOŚ", "LIFE", "calls"]),
    "technical_changes": ("Zmiany techniczne", ["API change", "outage", "new dataset", "schema"]),
}
ALIASES = {
    "inspire": "spatial_information",
    "gis": "geoportal_geodesy",
    "data": "open_data_api",
    "climate": "climate_adaptation",
    "air": "air_quality",
    "nature": "nature_biodiversity",
    "waste": "waste_circular",
}
