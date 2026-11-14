import io
import struct
from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from ingestion import effis_fwi_raster as fwi
from ingestion.data_service import run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import FetchError, Response

# Minimal valid GeoKey directory: geographic model, pixel-is-area, EPSG:4326.
GEOKEYS = struct.pack("<16H", 1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)


def tiff(value=lambda i: (i % 97) / 4, rows=8, west=13.9958, colormap=False):
    """Little-endian float32 strip TIFF shaped like the 2026-10-14 WMS GetMap probe."""
    width, height = fwi.WIDTH, fwi.HEIGHT
    pixels = struct.pack(f"<{width * height}f", *(value(i) for i in range(width * height)))
    strips = -(-height // rows)
    extra = [
        (33550, 12, struct.pack("<3d", 1 / 24, 1 / 24, 0)),
        (33922, 12, struct.pack("<6d", 0, 0, 0, west, 55.0, 0)),
        (34735, 3, GEOKEYS),
    ] + ([(320, 3, b"\0\0" * 768)] if colormap else [])
    entries = 10 + len(extra)
    data_start = 8 + 2 + entries * 12 + 4
    blobs, cursor = bytearray(), data_start

    def blob(raw):
        nonlocal cursor
        start = cursor
        blobs.extend(raw)
        cursor += len(raw)
        return start

    offsets_pos = blob(b"\0" * 4 * strips)
    counts_pos = blob(b"\0" * 4 * strips)
    extras = [(tag, kind, len(raw) // {3: 2, 12: 8}[kind], blob(raw)) for tag, kind, raw in extra]
    offsets = [cursor + i * rows * width * 4 for i in range(strips)]
    counts = [min(rows, height - i * rows) * width * 4 for i in range(strips)]
    start = offsets_pos - data_start
    blobs[start : start + 4 * strips] = struct.pack(f"<{strips}I", *offsets)
    start = counts_pos - data_start
    blobs[start : start + 4 * strips] = struct.pack(f"<{strips}I", *counts)

    def short(tag, number):
        return struct.pack("<HHIHH", tag, 3, 1, number, 0)

    tags = [
        struct.pack("<HHII", 256, 4, 1, width),
        struct.pack("<HHII", 257, 4, 1, height),
        short(258, 32),
        short(259, 1),
        short(262, 1),
        struct.pack("<HHII", 273, 4, strips, offsets_pos),
        short(277, 1),
        struct.pack("<HHII", 278, 4, 1, rows),
        struct.pack("<HHII", 279, 4, strips, counts_pos),
        short(339, 3),
    ] + [struct.pack("<HHII", tag, kind, n, pos) for tag, kind, n, pos in extras]
    tags.sort(key=lambda raw: struct.unpack_from("<H", raw)[0])
    directory = struct.pack("<H", entries) + b"".join(tags) + b"\0\0\0\0"
    return b"II*\x00" + struct.pack("<I", 8) + directory + bytes(blobs) + pixels


ZERO = tiff(value=lambda i: 0.0)


class FakeClient:
    max_bytes = 0

    def __init__(self, bodies=None, default=None):
        self.bodies, self.default, self.urls = dict(bodies or {}), default or ZERO, []

    def get(self, url):
        self.urls.append(url)
        for day, body in self.bodies.items():
            if url == fwi.request_url(day):
                return Response(200, body, {"content-type": "image/tiff"}, url)
        return Response(200, self.default, {"content-type": "image/tiff"}, url)


class Channel:
    url, config, region = fwi.BASE, dict(fwi.CONFIG), "PL"

    class registry:
        registry_id = 202


class RasterParsingTests(SimpleTestCase):
    def test_probe_shaped_file_is_summarised(self):
        result = fwi.parse_raster(tiff())
        self.assertTrue(result["crs_in_file"])
        self.assertFalse(result["nodata_declared"])
        self.assertEqual(result["fwi_max"], 24.0)
        self.assertEqual(result["zero_pixels"], len(range(0, 245 * 144, 97)))
        self.assertAlmostEqual(result["bbox_raw"][0], 13.9958)

    def test_layout_extent_or_value_changes_are_refused(self):
        for body in (
            tiff(west=-25.0),
            tiff(value=lambda i: -1.0),
            tiff(value=lambda i: float("nan")),
            tiff(value=lambda i: 400.0),
            tiff()[:5000],
            b"MM\x00*" + b"\0" * 100,
        ):
            with self.subTest(size=len(body)), self.assertRaises(FetchError):
                fwi.parse_raster(body)

    def test_all_zero_day_is_skipped_for_the_previous_one(self):
        body = tiff()
        client = FakeClient({date(2026, 10, 13): body})
        payload, media, summary = fwi.collect(Channel, client, date(2026, 10, 14))
        self.assertEqual((payload, media), (body, "image/tiff"))
        self.assertEqual(summary["product_date"], "2026-10-13")
        self.assertEqual(client.urls, [fwi.request_url(date(2026, 10, d)) for d in (14, 13)])
        self.assertTrue(all("&TIME=" in url for url in client.urls))
        self.assertNotIn("newest_observation", summary)

    def test_no_values_in_range_fails(self):
        client = FakeClient()
        with self.assertRaisesMessage(FetchError, "No EFFIS FWI raster"):
            fwi.collect(Channel, client, date(2026, 10, 14))
        self.assertEqual(len(client.urls), fwi.CONFIG["days_back"] + 1)

    def test_changed_profile_is_refused_before_http(self):
        class Changed(Channel):
            config = {**fwi.CONFIG, "days_back": 30}

        client = FakeClient()
        with self.assertRaises(FetchError):
            fwi.collect(Changed, client, date(2026, 10, 14))
        self.assertEqual(client.urls, [])


class ChannelTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t", sha256="c" * 64, sheets={})
        RegistryEntry.objects.create(
            registry_id=202, release=release, priority="P2", master={}, technical={}
        )

    def test_configure_is_dry_run_by_default_then_creates_disabled(self):
        call_command("configure_effis_fwi_raster", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_effis_fwi_raster", "--apply", stdout=io.StringIO())
        channel = DataChannel.objects.get(slug=fwi.SLUG)
        self.assertFalse(channel.enabled or channel.access_approved)
        self.assertEqual((channel.interval_minutes, channel.region), (1440, "PL"))
        self.assertIn(
            "Copernicus Emergency Management Service information 2026", channel.attribution
        )
        DataChannel.objects.filter(pk=channel.pk).update(config={"mode": "other"})
        with self.assertRaises(CommandError):
            call_command("configure_effis_fwi_raster", "--apply", stdout=io.StringIO())

    def test_worker_run_stores_new_day_and_dedupes_same_day(self):
        call_command("configure_effis_fwi_raster", "--apply", stdout=io.StringIO())
        DataChannel.objects.filter(slug=fwi.SLUG).update(enabled=True, access_approved=True)
        client = FakeClient(default=tiff())
        with patch("ingestion.data_service.Client", return_value=client):
            first = run_data(fwi.SLUG, force=True)
            second = run_data(fwi.SLUG, force=True)
        self.assertEqual([first[0]["status"], second[0]["status"]], ["stored", "unchanged"])
        snapshot = DataSnapshot.objects.get()
        self.assertEqual(snapshot.summary["url"], client.urls[0])
        self.assertEqual(snapshot.media_type, "image/tiff")
