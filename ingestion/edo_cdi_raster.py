"""EDO Combined Drought Indicator (cdiad) GeoTIFF for the Polish longitude band (registry 200).

User accepted the Copernicus CEMS data terms for EDO CDI rasters on 2026-10-14T09:06Z.
One reviewed WCS 2.0 GetCoverage per product date, EPSG:4326, longitude subset 14-24.2.
The provider ignores a latitude subset (probe 2026-10-14), so the copy keeps lat 22-72.
The file carries no date: the product date is the newest dekad (1/11/21) the service
accepts via TIME, tried newest first; HTTP 422 means "not published yet". Since
2026-11 the provider answers HTTP 400 with an OWS InvalidParameterValue exception for
locator "time" instead (PC probe 2026-11-05); only that exact report counts as
"not published yet", any other 400 still stops the run. The original
provider bytes are stored unchanged. Class semantics (0-6) follow the CDI factsheet but
stay UNCONFIRMED in the copy; class 0 may mean "no drought" or "no data".
"""

import hashlib
import struct
from datetime import date, timedelta

import numpy
import rasterio
from rasterio.enums import Compression
from rasterio.errors import RasterioError
from rasterio.io import MemoryFile

from ingestion.transport import FetchError

CONFIG_MODE = "edo_cdi_raster"
REGISTRY_ID = 200
SLUG = "registry-200-edo-cdi-pl"
NAME = "EDO — wskaźnik suszy CDI (cdiad), pas Polski, GeoTIFF"
BASE = (
    "https://drought.emergency.copernicus.eu/api/wcs?map=DO_WCS&SERVICE=WCS&VERSION=2.0.0"
    "&REQUEST=GetCoverage&coverageID=cdiad&CRS=EPSG:4326&format=GEOTIFF"
)
LON_MIN, LON_MAX = 14, 24.2
SUBSET = f"&subset=Long({LON_MIN},{LON_MAX})"
CONFIG = {"mode": CONFIG_MODE, "coverage": "cdiad", "lon": [LON_MIN, LON_MAX], "dekads_back": 9}
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 60
ATTRIBUTION = (
    "Generated using Copernicus Emergency Management Service information {year}. "
    "European Drought Observatory (EDO), Combined Drought Indicator (CDI) cdiad, WCS GeoTIFF; "
    "oryginalne bajty dostawcy, wycinek długości 14–24,2°E. Dane bez gwarancji (warunki CEMS)."
)
CLASSES = set(range(7))


def request_url(day):
    return f"{BASE}&TIME={day.isoformat()}{SUBSET}"


def is_request(url):
    return url.startswith(BASE + "&TIME=") and url.endswith(SUBSET)


def dekads(today, count):
    """Newest-first dekad start dates (1, 11, 21) on or before today."""
    day = date(today.year, today.month, 21 if today.day >= 21 else 11 if today.day >= 11 else 1)
    result = []
    for _ in range(count):
        result.append(day)
        if day.day == 21:
            day = day.replace(day=11)
        elif day.day == 11:
            day = day.replace(day=1)
        else:
            previous = day - timedelta(days=1)
            day = previous.replace(day=21)
    return result


def validate_profile(channel):
    if (
        channel.url != BASE
        or channel.config != CONFIG
        or channel.region != "PL"
        or channel.registry.registry_id != REGISTRY_ID
    ):
        raise FetchError("EDO CDI raster profile changed; review required")


def read_band(payload, label, dtype):
    """Open provider bytes with rasterio (GDAL) and return the single band with its grid.

    Shared with the EFFIS FWI raster. Only little-endian TIFF with one band of the
    reviewed type is accepted; raw and Deflate strips are the reviewed encodings.
    """
    if payload[:4] != b"II*\x00":
        raise FetchError(f"{label} response is not a little-endian TIFF")
    try:
        # The copies are compared by grid origin as stored in the file, so GDAL must not
        # shift PixelIsPoint rasters by half a pixel.
        with (
            rasterio.Env(GTIFF_POINT_GEO_IGNORE=True),
            MemoryFile(payload) as memory,
            memory.open(driver="GTiff") as dataset,
        ):
            if (
                dataset.count != 1
                or dataset.dtypes[0] != dtype
                or dataset.compression not in (None, Compression.deflate)
                or not 0 < dataset.width <= 4000
                or not 0 < dataset.height <= 4000
            ):
                raise FetchError(f"{label} TIFF band layout changed")
            transform = dataset.transform
            if transform.b or transform.d or transform.a <= 0 or transform.e >= 0:
                raise FetchError(f"{label} georeferencing missing")
            return {
                "pixels": dataset.read(1),
                "width": dataset.width,
                "height": dataset.height,
                "scale": (transform.a, -transform.e),
                "west": transform.c,
                "north": transform.f,
                "crs_in_file": dataset.crs is not None,
                "nodata_declared": dataset.nodata is not None,
            }
    except RasterioError as exc:
        raise FetchError(f"{label} TIFF not readable") from exc


def grid_hash(band):
    """SHA-256 over the grid header and decoded pixels, unchanged since the first copies."""
    header = struct.pack(
        "<2I6d", band["width"], band["height"], *band["scale"], 0, band["west"], band["north"], 0
    )
    return hashlib.sha256(header + band["pixels"].tobytes()).hexdigest()


def parse_raster(payload):
    """Validate the reviewed single-band uint8 grid and count classes. No writes."""
    if not 0 < len(payload) <= MAX_BYTES:
        raise FetchError("EDO CDI raster size outside budget")
    band = read_band(payload, "EDO CDI", "uint8")
    width, height, scale = band["width"], band["height"], band["scale"]
    west, north = band["west"], band["north"]
    east, south = west + scale[0] * width, north - scale[1] * height
    if (
        abs(west - LON_MIN) > 0.05
        or abs(east - LON_MAX) > 0.05
        or not 54 <= north <= 72.01
        or not 21.99 <= south <= 49
        or abs(scale[1] - 1 / 24) > 1e-4
    ):
        raise FetchError("EDO CDI raster extent changed")
    values, counts = numpy.unique(band["pixels"], return_counts=True)
    found = {int(value): int(count) for value, count in zip(values, counts, strict=True)}
    if not set(found) <= CLASSES:
        raise FetchError("EDO CDI class values changed; legend review required")
    return {
        "width": width,
        "height": height,
        "bbox_raw": [west, south, east, north],
        "crs_in_file": band["crs_in_file"],
        "nodata_declared": band["nodata_declared"],
        "class_counts": {str(value): n for value, n in sorted(found.items())},
        # Decoded pixels, so a Deflate copy of the same grid deduplicates against raw copies.
        "pixels_sha256": grid_hash(band),
    }


def time_not_published(exc):
    """True for the provider's not-yet-published answer, never for other HTTP errors."""
    if exc.status == 422:
        return True
    body = getattr(exc, "body", b"") or b""
    return (
        exc.status == 400
        and b"ExceptionReport" in body
        and b'exceptionCode="InvalidParameterValue"' in body
        and b'locator="time"' in body
    )


def collect(channel, client, today):
    validate_profile(channel)
    client.max_bytes = MAX_BYTES
    for day in dekads(today, CONFIG["dekads_back"]):
        url = request_url(day)
        try:
            response = client.get(url)
        except FetchError as exc:
            if time_not_published(exc):  # The next older dekad is tried.
                continue
            raise
        payload = response.body
        summary = {
            "kind": "edo_cdi_raster",
            "scope": "PL",
            "collector": CONFIG_MODE,
            "coverage": "cdiad",
            "product_date": day.isoformat(),
            "date_method": "newest dekad accepted by WCS TIME",
            "url": url,
            "original_response_preserved": True,
            "provider_media_type": response.headers.get("content-type", "")[:100],
            "sha256": hashlib.sha256(payload).hexdigest(),
            "semantic_status": "unconfirmed",
            "warnings": [
                "categorical_not_physical_measurement",
                "class_0_meaning_unconfirmed",
                "latitude_subset_ignored_by_provider",
                "wcs_uom_mismatch",
            ],
            **parse_raster(payload),
        }
        # The provider omits GeoKeys in some responses for the same product (2026-10-14),
        # so the whole-file SHA varies; dedupe compares product date, grid and pixels.
        key = f"{day.isoformat()}:{summary['pixels_sha256']}".encode()
        summary["content_sha256"] = hashlib.sha256(key).hexdigest()
        if not summary["crs_in_file"]:
            summary["warnings"].append("crs_missing_in_file_epsg4326_requested")
        return payload, "image/tiff", summary
    raise FetchError("No CDI product date accepted in the reviewed dekad range")
