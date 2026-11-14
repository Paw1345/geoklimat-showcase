"""Check installed PL/EU boundary masks: manifest, SHA-256, CRS and topology."""

import json

from django.core.management.base import BaseCommand, CommandError

from ingestion.boundary_mask import REGIONS, MaskError, load_mask, read_manifest


class Command(BaseCommand):
    help = "Verify pinned boundary masks without HTTP, reprojection or repair."

    def add_arguments(self, parser):
        parser.add_argument("--region", choices=sorted(REGIONS))
        parser.add_argument("--manifest")

    def handle(self, *args, **options):
        try:
            _, entries = read_manifest(options["manifest"])
            regions = [options["region"]] if options["region"] else sorted(entries)
            masks = [load_mask(region, options["manifest"]).provenance() for region in regions]
        except MaskError as exc:
            raise CommandError(f"{exc.reason}: {exc}") from exc
        self.stdout.write(json.dumps({"masks": masks}, ensure_ascii=False, indent=2))
