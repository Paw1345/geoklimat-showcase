"""Fetch or read and pin a reviewed boundary mask; dry-run by default."""

import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.boundary_egm import DEFAULT_FIELD, DEFAULT_LAYER, EGM, inspect, read_eu
from ingestion.boundary_mask import MaskError, install
from ingestion.boundary_sources import NATURAL_EARTH, PRG, PRG_TYPE, fetch_naturalearth, fetch_prg
from ingestion.transport import Client, FetchError

SOURCES = {"PL": ("prg",), "EU": ("naturalearth", "egm")}


class Command(BaseCommand):
    help = (
        "Pin a boundary mask: PL from the PRG WFS (GUGiK); EU from Natural Earth admin-0 "
        "(public domain) or a locally downloaded EuroGlobalMap GeoPackage. Dry-run default, "
        "never repairs, keeps older mask files."
    )

    def add_arguments(self, parser):
        parser.add_argument("--region", choices=sorted(SOURCES), required=True)
        parser.add_argument(
            "--source", choices=sorted(s for v in SOURCES.values() for s in v), required=True
        )
        parser.add_argument("--type-name", default=PRG_TYPE)
        parser.add_argument("--file", help="EuroGlobalMap .gpkg downloaded by the operator")
        parser.add_argument("--layer", default=DEFAULT_LAYER)
        parser.add_argument("--country-field", default=DEFAULT_FIELD)
        parser.add_argument("--release", dest="mask_version", help="Release, e.g. EGM 2025")
        parser.add_argument("--licence", help="Licence as reviewed on the download page")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        region, source = options["region"], options["source"]
        if source not in SOURCES[region]:
            raise CommandError(f"{region} mask comes from {' or '.join(SOURCES[region])}")
        try:
            if source == "prg":
                document, details = fetch_prg(Client(), options["type_name"])
                meta = PRG | {"slug": "prg"}
            elif source == "naturalearth":
                document, details = fetch_naturalearth(Client()), None
                meta = NATURAL_EARTH | {"slug": "naturalearth"}
            else:
                if not (options["file"] and options["mask_version"] and options["licence"]):
                    raise CommandError("EGM needs --file, --release and the reviewed --licence")
                try:
                    document, details = read_eu(
                        options["file"], options["layer"], options["country_field"]
                    )
                except FetchError as exc:
                    layers = inspect(options["file"]) if "not in GeoPackage" in str(exc) else []
                    raise CommandError(f"{exc}; layers: {json.dumps(layers)}") from exc
                details["layer"] = options["layer"]
                meta = EGM | {
                    "slug": "egm",
                    "version": options["mask_version"],
                    "licence": options["licence"],
                }
            result = install(region, document, meta, apply=options["apply"])
        except (FetchError, MaskError) as exc:
            raise CommandError(str(exc)) from exc
        if details:
            result["source_details"] = details
        if not result["changed"]:
            self.stdout.write("Same mask already installed; no changes")
        elif not result["applied"]:
            self.stdout.write("Dry-run; no writes")
        self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
