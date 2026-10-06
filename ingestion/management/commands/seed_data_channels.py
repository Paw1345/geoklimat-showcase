from django.core.management.base import BaseCommand, CommandError

from ingestion.data_service import query_url
from ingestion.models import DataChannel, RegistryEntry


class Command(BaseCommand):
    help = "Create reviewed public data profiles, disabled until a successful audit."

    def handle(self, *args, **options):
        if not RegistryEntry.objects.exists():
            raise CommandError("Import the workbook first")
        attribution = (
            "Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej "
            "– Państwowy Instytut Badawczy. Dane przefiltrowano do ostatnich 30 dni. "
            "Warunki ponownego wykorzystania: https://danepubliczne.imgw.pl/pl/apiinfo"
        )
        for slug, registry_id in [("synop", 34), ("meteo", 34), ("hydro", 33)]:
            DataChannel.objects.get_or_create(
                slug="imgw-" + slug,
                defaults=dict(
                    registry=RegistryEntry.objects.get(registry_id=registry_id),
                    name="IMGW — pomiary " + slug,
                    kind="imgw",
                    url="https://danepubliczne.imgw.pl/api/data/" + slug,
                    region="PL",
                    attribution=attribution,
                ),
            )
        DataChannel.objects.get_or_create(
            slug="copernicus-scenes-pl",
            defaults=dict(
                registry=RegistryEntry.objects.get(registry_id=191),
                name="Copernicus — katalog scen nad Polską (bbox, próbka)",
                kind="stac",
                region="PL",
                url="https://stac.dataspace.copernicus.eu/v1/search",
                config={"bbox": [14.12, 49.0, 24.15, 54.84], "collection": "sentinel-2-l2a"},
                attribution="Copernicus Data Space. Prostokąt obejmuje również pogranicze; "
                "to nie dokładna granica administracyjna. Licencje są zależne od kolekcji. "
                "Pliki obrazowe wyłącznie na żądanie.",
            ),
        )
        DataChannel.objects.get_or_create(
            slug="gugik-named-places",
            defaults=dict(
                registry=RegistryEntry.objects.get(registry_id=53),
                name="GUGiK — nazwy geograficzne z geometrią (próbka WFS)",
                kind="geojson",
                region="PL",
                interval_minutes=1440,
                url=query_url(
                    "https://mapy.geoportal.gov.pl/wss/service/wfsGN/guest",
                    SERVICE="WFS",
                    VERSION="2.0.0",
                    REQUEST="GetFeature",
                    TYPENAMES="gn:NamedPlace",
                    COUNT=100,
                    OUTPUTFORMAT="application/json",
                    SRSNAME="http://www.opengis.net/def/crs/EPSG/0/4326",
                ),
                config={"feature_limit": 100},
                attribution="GUGiK / Geoportal — WFS GN. Pierwsze 100 obiektów, nie pełny PRNG. "
                "Geometrie i atrybuty zachowane z odpowiedzi usługi.",
            ),
        )
        DataChannel.objects.get_or_create(
            slug="copernicus-scenes-europe",
            defaults=dict(
                registry=RegistryEntry.objects.get(registry_id=191),
                name="Copernicus — sceny nad Europą (przybliżenie obszaru UE, próbka)",
                kind="stac",
                region="EU",
                url="https://stac.dataspace.copernicus.eu/v1/search",
                config={"bbox": [-31.3, 27.5, 35.7, 70.1], "collection": "sentinel-2-l2a"},
                attribution="Copernicus Data Space. Prostokąt Europy obejmuje kraje spoza UE; "
                "nie obejmuje wszystkich terytoriów zamorskich. Dokładna maska UE "
                "wymaga osobnego zbioru granic. Katalog, nie lokalne obrazy.",
            ),
        )
        RegistryEntry.objects.filter(
            registry_id__in=[33, 34], integration_state="needs_connector"
        ).update(
            integration_state="data_configured",
            integration_note="Osobne publiczne kanały pomiarowe IMGW w module Dane i GIS. "
            "Stan aktywacji i audytu znajduje się w profilu danych.",
        )
        self.stdout.write(
            f"Data channels: {DataChannel.objects.count()}; existing settings preserved"
        )
