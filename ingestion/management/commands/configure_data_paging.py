from urllib.parse import parse_qsl, urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ingestion.models import DataChannel
from ingestion.service import batch_lock


class Command(BaseCommand):
    help = "Enable resumable paging for the three reviewed GIS/STAC profiles; dry-run default."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        profiles = {
            "copernicus-scenes-pl": {
                "bbox": [14.12, 49.0, 24.15, 54.84],
                "collection": "sentinel-2-l2a",
            },
            "copernicus-scenes-europe": {
                "bbox": [-31.3, 27.5, 35.7, 70.1],
                "collection": "sentinel-2-l2a",
            },
            "gugik-named-places": {"feature_limit": 100},
        }
        with batch_lock() as acquired, transaction.atomic():
            if not acquired:
                raise CommandError("Collector busy; retry later")
            for slug, old_config in profiles.items():
                channel = DataChannel.objects.filter(slug=slug).first()
                if not channel:
                    raise CommandError("Run seed_data_channels first")
                mode = "wfs2" if slug == "gugik-named-places" else "stac_get"
                config = {**old_config, "pagination": mode}
                if channel.config == config:
                    self.stdout.write(f"{slug}: unchanged")
                    continue
                url = urlsplit(channel.url)
                expected_host = (
                    "mapy.geoportal.gov.pl" if mode == "wfs2" else "stac.dataspace.copernicus.eu"
                )
                expected_path = "/wss/service/wfsGN/guest" if mode == "wfs2" else "/v1/search"
                if (
                    channel.config != old_config
                    or url.scheme != "https"
                    or url.netloc != expected_host
                    or url.path != expected_path
                    or url.fragment
                    or channel.kind != ("geojson" if mode == "wfs2" else "stac")
                ):
                    self.stdout.write(f"{slug}: skipped modified configuration")
                    continue
                if mode == "wfs2":
                    expected_query = {
                        "SERVICE": "WFS",
                        "VERSION": "2.0.0",
                        "REQUEST": "GetFeature",
                        "TYPENAMES": "gn:NamedPlace",
                        "COUNT": "100",
                        "OUTPUTFORMAT": "application/json",
                        "SRSNAME": "http://www.opengis.net/def/crs/EPSG/0/4326",
                    }
                    if sorted(parse_qsl(url.query)) != sorted(expected_query.items()):
                        self.stdout.write(f"{slug}: skipped modified query")
                        continue
                elif url.query:
                    self.stdout.write(f"{slug}: skipped modified query")
                    continue
                self.stdout.write(
                    f"{slug}: add {mode}; disabled pending audit; applied={options['apply']}"
                )
                if options["apply"]:
                    channel.config = config
                    channel.enabled = channel.access_approved = False
                    channel.last_status, channel.last_error, channel.checked_at = (
                        "needs_audit",
                        "",
                        None,
                    )
                    channel.name = channel.name.replace("próbka WFS", "paginowane WFS").replace(
                        "próbka", "paginowane"
                    )
                    channel.attribution = channel.attribution.replace(
                        "Pierwsze 100 obiektów, nie pełny PRNG.",
                        "Do 100 obiektów na stronę; postęp nie oznacza jeszcze pełnego PRNG.",
                    )
                    channel.attribution += (
                        " Paginacja: kolejne strony są zapisywane osobno "
                        "z postępem w module danych."
                    )
                    channel.save()
