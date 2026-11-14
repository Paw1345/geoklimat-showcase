from django.core.management.base import BaseCommand, CommandError

from ingestion import edo_cdi_raster, effis_fwi_raster
from ingestion.models import DataSnapshot
from ingestion.transport import FetchError

PARSERS = {
    edo_cdi_raster.CONFIG_MODE: edo_cdi_raster.parse_raster,
    effis_fwi_raster.CONFIG_MODE: effis_fwi_raster.parse_raster,
}


class Command(BaseCommand):
    help = (
        "Read-only: re-read stored EDO CDI and EFFIS FWI GeoTIFF copies with the current "
        "reader and compare the result with the summary saved at collection time. "
        "No HTTP and no writes."
    )

    def handle(self, *args, **options):
        checked = different = 0
        copies = DataSnapshot.objects.filter(summary__collector__in=PARSERS).order_by("pk")
        for copy in copies.iterator(chunk_size=20):
            checked += 1
            try:
                current = PARSERS[copy.summary["collector"]](bytes(copy.payload))
            except FetchError as exc:
                current = {"error": str(exc)}
            changed = sorted(
                key
                for key in current
                if key == "error" or (key in copy.summary and copy.summary[key] != current[key])
            )
            if changed:
                different += 1
                self.stdout.write(f"copy {copy.pk}: differs in {', '.join(changed)}")
        self.stdout.write(f"Checked {checked} copies; different {different}.")
        if different:
            raise CommandError("Stored raster copies do not match the current reader")
