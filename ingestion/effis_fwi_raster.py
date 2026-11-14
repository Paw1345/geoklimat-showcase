"""EFFIS Fire Weather Index (layer mf010.fwi) float32 GeoTIFF for Poland (registry 202).

User accepted the Copernicus CEMS data terms for EFFIS FWI rasters on 2026-10-14T09:06Z.
EFFIS WCS offers no FWI coverage (1.0.0 lists only fuel_map, 2.0.1 answers 502), but a WMS
GetMap with FORMAT=image/tiff returns raw float32 FWI values (probe 2026-10-14T11:25Z).
One reviewed GetMap per day, EPSG:4326, BBOX 49-55N 14-24.2E, 245x144 (1/24 degree).
TIME is mandatory: without it, or for an unpublished day, every pixel is 0.0, so an
all-zero raster counts as "not available" and the previous day is tried. The original
provider bytes are stored unchanged. The forecast lead time behind mf010, the meaning of
0.0 (sea / outside the model) and the source grid stay UNCONFIRMED.
"""

import hashlib
from datetime import timedelta

import numpy

from ingestion.edo_cdi_raster import grid_hash, read_band
from ingestion.transport import FetchError

CONFIG_MODE = "effis_fwi_raster"
REGISTRY_ID = 202
SLUG = "registry-202-effis-fwi-pl"
NAME = "EFFIS — wskaźnik pogody pożarowej FWI (mf010.fwi), Polska, GeoTIFF"
LAYER = "mf010.fwi"
WIDTH, HEIGHT = 245, 144
BASE = (
    "https://maps.effis.emergency.copernicus.eu/effis?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
    f"&LAYERS={LAYER}&STYLES=&FORMAT=image/tiff&CRS=EPSG:4326&BBOX=49,14,55,24.2"
    f"&WIDTH={WIDTH}&HEIGHT={HEIGHT}"
)
CONFIG = {"mode": CONFIG_MODE, "layer": LAYER, "bbox": [49, 14, 55, 24.2], "days_back": 3}
MAX_BYTES = 512 * 1024
MAX_FWI = 250.0
ATTRIBUTION = (
    "Generated using Copernicus Emergency Management Service information {year}. "
    "European Forest Fire Information System (EFFIS), Fire Weather Index mf010.fwi, "
    "WMS GetMap GeoTIFF float32; oryginalne bajty dostawcy, BBOX 49–55°N 14–24,2°E. "
    "Dane bez gwarancji (warunki CEMS)."
)


def request_url(day):
    return f"{BASE}&TIME={day.isoformat()}"


def validate_profile(channel):
    if (
        channel.url != BASE
        or channel.config != CONFIG
        or channel.region != "PL"
        or channel.registry.registry_id != REGISTRY_ID
    ):
        raise FetchError("EFFIS FWI raster profile changed; review required")


def parse_raster(payload):
    """Validate the reviewed float32 grid and summarise values. No writes."""
    if not 0 < len(payload) <= MAX_BYTES:
        raise FetchError("EFFIS FWI raster size outside budget")
    band = read_band(payload, "EFFIS FWI", "float32")
    if (band["width"], band["height"]) != (WIDTH, HEIGHT):
        raise FetchError("EFFIS FWI TIFF band layout changed")
    scale, west, north = band["scale"], band["west"], band["north"]
    east, south = west + scale[0] * WIDTH, north - scale[1] * HEIGHT
    if (
        abs(west - 14) > 0.05
        or abs(east - 24.2) > 0.05
        or abs(north - 55) > 0.05
        or abs(south - 49) > 0.05
        or abs(scale[0] - 1 / 24) > 1e-3
        or abs(scale[1] - 1 / 24) > 1e-3
    ):
        raise FetchError("EFFIS FWI raster extent changed")
    values = band["pixels"]
    if not numpy.isfinite(values).all() or values.min() < 0 or values.max() > MAX_FWI:
        raise FetchError("EFFIS FWI values outside 0..250; review required")
    # Python floats keep the rounding of the earlier copies' summaries.
    positive = [float(value) for value in values[values > 0]]
    return {
        "width": WIDTH,
        "height": HEIGHT,
        "bbox_raw": [west, south, east, north],
        "crs_in_file": band["crs_in_file"],
        "nodata_declared": band["nodata_declared"],
        "zero_pixels": int(values.size) - len(positive),
        "fwi_min_positive": round(min(positive), 3) if positive else None,
        "fwi_max": round(float(values.max()), 3),
        "fwi_mean_positive": round(sum(positive) / len(positive), 3) if positive else None,
        "pixels_sha256": grid_hash(band),
    }


def collect(channel, client, today):
    validate_profile(channel)
    client.max_bytes = MAX_BYTES
    for back in range(CONFIG["days_back"] + 1):
        day = today - timedelta(days=back)
        url = request_url(day)
        response = client.get(url)
        payload = response.body
        parsed = parse_raster(payload)
        if parsed["fwi_mean_positive"] is None:
            continue  # All 0.0: the provider's answer for a day it does not have.
        summary = {
            "kind": "effis_fwi_raster",
            "scope": "PL",
            "collector": CONFIG_MODE,
            "layer": LAYER,
            "product_date": day.isoformat(),
            "date_method": "WMS TIME requested; all-zero raster treated as unavailable",
            "url": url,
            "original_response_preserved": True,
            "provider_media_type": response.headers.get("content-type", "")[:100],
            "sha256": hashlib.sha256(payload).hexdigest(),
            "semantic_status": "unconfirmed",
            "warnings": [
                "wms_rendering_not_wcs",
                "forecast_lead_time_unconfirmed",
                "zero_meaning_unconfirmed",
                "grid_half_pixel_shift_vs_bbox",
            ],
            **parsed,
        }
        key = f"{day.isoformat()}:{parsed['pixels_sha256']}".encode()
        summary["content_sha256"] = hashlib.sha256(key).hexdigest()
        return payload, "image/tiff", summary
    raise FetchError("No EFFIS FWI raster with values in the reviewed day range")
