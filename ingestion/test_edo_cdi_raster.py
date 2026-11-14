import io
import struct
import zlib
from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from ingestion import edo_cdi_raster as cdi
from ingestion.data_service import run_data
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.transport import Client, FetchError, Response, request_timeout

# Minimal valid GeoKey directory: geographic model, pixel-is-area, EPSG:4326.
GEOKEYS = struct.pack("<16H", 1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326)


def tiff(
    width=245,
    height=1200,
    rows=33,
    west=14.0,
    north=72.0,
    values=(0, 1, 2, 6),
    crs=True,
    compression=1,
    predictor=None,
    encode=None,
):
    """Little-endian strip TIFF shaped like the 2026-10-14 probe; Deflate like 2026-11-05."""
    pixels = bytes(values[i % len(values)] for i in range(width * height))
    strips = -(-height // rows)
    raw_strips = [pixels[i * rows * width : (i + 1) * rows * width] for i in range(strips)]
    if compression == 8:
        raw_strips = [(encode or zlib.compress)(strip) for strip in raw_strips]
        pixels = b"".join(raw_strips)
    scale = (cdi.LON_MAX - cdi.LON_MIN) / width
    extra = [
        (33550, 12, struct.pack("<3d", scale, 1 / 24, 0)),
        (33922, 12, struct.pack("<6d", 0, 0, 0, west, north, 0)),
    ] + ([(34735, 3, GEOKEYS)] if crs else [])
    entries = 9 + len(extra) + (predictor is not None)
    ifd = 8
    data_start = ifd + 2 + entries * 12 + 4
    blobs, tags, cursor = b"", [], data_start

    def blob(raw):
        nonlocal blobs, cursor
        start = cursor
        blobs += raw
        cursor += len(raw)
        return start

    offsets_pos = blob(b"\0" * 4 * strips)
    counts_pos = blob(b"\0" * 4 * strips)
    extras = [(tag, kind, len(raw) // {3: 2, 12: 8}[kind], blob(raw)) for tag, kind, raw in extra]
    pixel_start = cursor
    if compression == 8:
        counts = [len(strip) for strip in raw_strips]
        offsets = [pixel_start + sum(counts[:i]) for i in range(strips)]
    else:
        offsets = [pixel_start + i * rows * width for i in range(strips)]
        counts = [min(rows, height - i * rows) * width for i in range(strips)]
        counts[-1] = 459603969  # The implausible last value seen in the live probe.
    blobs = bytearray(blobs)
    blobs[offsets_pos - data_start : offsets_pos - data_start + 4 * strips] = struct.pack(
        f"<{strips}I", *offsets
    )
    blobs[counts_pos - data_start : counts_pos - data_start + 4 * strips] = struct.pack(
        f"<{strips}I", *counts
    )

    def short(tag, value):
        return struct.pack("<HHIHH", tag, 3, 1, value, 0)

    tags = [
        struct.pack("<HHII", 256, 4, 1, width),
        struct.pack("<HHII", 257, 4, 1, height),
        short(258, 8),
        short(259, compression),
        struct.pack("<HHII", 273, 4, strips, offsets_pos),
        short(277, 1),
        struct.pack("<HHII", 278, 4, 1, rows),
        struct.pack("<HHII", 279, 4, strips, counts_pos),
        short(339, 1),
    ]
    if predictor is not None:
        tags.append(short(317, predictor))
    tags += [struct.pack("<HHII", tag, kind, n, pos) for tag, kind, n, pos in extras]
    header = b"II*\x00" + struct.pack("<I", ifd)
    directory = struct.pack("<H", entries) + b"".join(tags) + b"\0\0\0\0"
    return header + directory + bytes(blobs) + pixels


class FakeClient:
    max_bytes = 0

    def __init__(self, published, body=None, error=None, bodies=None):
        self.published, self.body, self.error, self.urls = published, body or tiff(), error, []
        self.bodies = list(bodies or [])

    def get(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        if url != cdi.request_url(self.published):
            raise FetchError("Unexpected HTTP response", 422)
        body = self.bodies.pop(0) if self.bodies else self.body
        return Response(200, body, {"content-type": "application/octet-stream"}, url)


# Shape of the provider answer for a dekad not yet published (PC probe 2026-11-05).
OWS_TIME_400 = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<ows:ExceptionReport '
    b'xmlns:ows="http://www.opengis.net/ows/2.0" version="2.0.1" xml:lang="en-US">\n'
    b'<ows:Exception exceptionCode="InvalidParameterValue" locator="time">\n'
    b"<ows:ExceptionText>msWCSGetCoverage20(): WCS server error. Requested time is "
    b"outside the coverage time extent.</ows:ExceptionText>\n</ows:Exception>\n"
    b"</ows:ExceptionReport>"
)


def http_error(status, body):
    error = FetchError("Unexpected HTTP response", status)
    error.body = body
    return error


class OwsClient(FakeClient):
    """Newer dekads answer 400 with a body, as the provider did from 2026-11."""

    def __init__(self, published, rejection=OWS_TIME_400, status=400):
        super().__init__(published)
        self.rejection, self.status = rejection, status

    def get(self, url):
        self.urls.append(url)
        if url != cdi.request_url(self.published):
            raise http_error(self.status, self.rejection)
        return Response(200, self.body, {"content-type": "application/octet-stream"}, url)


class Channel:
    url, config, region = cdi.BASE, dict(cdi.CONFIG), "PL"

    class registry:
        registry_id = 200


class RasterParsingTests(SimpleTestCase):
    def test_dekads_newest_first_across_month_boundary(self):
        self.assertEqual(
            cdi.dekads(date(2026, 10, 14), 5),
            [date(2026, 10, 11), date(2026, 10, 1), date(2026, 9, 21), date(2026, 9, 11),
             date(2026, 9, 1)],
        )  # fmt: skip
        self.assertEqual(cdi.dekads(date(2026, 1, 3), 2), [date(2026, 1, 1), date(2025, 12, 21)])

    def test_probe_shaped_file_is_counted_without_trusting_last_strip_count(self):
        result = cdi.parse_raster(tiff())
        self.assertEqual((result["width"], result["height"]), (245, 1200))
        self.assertEqual(sum(result["class_counts"].values()), 245 * 1200)
        self.assertEqual(set(result["class_counts"]), {"0", "1", "2", "6"})
        self.assertTrue(result["crs_in_file"])
        self.assertFalse(result["nodata_declared"])
        self.assertAlmostEqual(result["bbox_raw"][1], 22.0)

    def test_unknown_class_extent_or_format_change_is_refused(self):
        for body in (
            tiff(values=(0, 7)),
            tiff(west=-25.0),
            tiff(height=100),
            b"MM\x00*" + b"\0" * 100,
            tiff()[:5000],
        ):
            with self.subTest(size=len(body)), self.assertRaises(FetchError):
                cdi.parse_raster(body)

    def test_deflate_strips_decode_to_same_pixels_and_hash(self):
        plain = cdi.parse_raster(tiff())
        packed_body = tiff(compression=8, predictor=1)
        self.assertLess(len(packed_body), len(tiff()) // 10)
        packed = cdi.parse_raster(packed_body)
        self.assertEqual(packed, plain)
        self.assertEqual(cdi.parse_raster(tiff(compression=8)), plain)

    def test_deflate_variants_outside_review_are_refused(self):
        def truncated(strip):
            return zlib.compress(strip)[:-6]

        def short_strip(strip):
            return zlib.compress(strip[:-1])

        bodies = {
            "predictor 2": tiff(compression=8, predictor=2),
            "lzw": tiff(compression=5),
            "truncated": tiff(compression=8, encode=truncated),
            "short": tiff(compression=8, encode=short_strip),
            "garbage": tiff(compression=8, encode=lambda strip: b"x" * 40),
            "class 7": tiff(compression=8, values=(0, 7)),
        }
        for name, body in bodies.items():
            with self.subTest(name), self.assertRaises(FetchError):
                cdi.parse_raster(body)

    def test_newest_published_dekad_is_selected_and_original_bytes_kept(self):
        body = tiff()
        client = FakeClient(date(2026, 9, 11), body)
        payload, media, summary = cdi.collect(Channel, client, date(2026, 10, 14))
        self.assertEqual(payload, body)
        self.assertEqual(media, "image/tiff")
        self.assertEqual(summary["product_date"], "2026-09-11")
        self.assertEqual(len(client.urls), 4)
        self.assertTrue(all(cdi.is_request(url) for url in client.urls))
        self.assertNotIn("Lat(", client.urls[0])
        self.assertNotIn("newest_observation", summary)
        self.assertIn("class_0_meaning_unconfirmed", summary["warnings"])

    def test_other_errors_and_exhausted_range_fail(self):
        with self.assertRaises(FetchError) as caught:
            cdi.collect(
                Channel, FakeClient(None, error=FetchError("down", 503)), date(2026, 10, 14)
            )
        self.assertEqual(caught.exception.status, 503)
        with self.assertRaisesMessage(FetchError, "No CDI product date"):
            cdi.collect(Channel, FakeClient(date(2025, 1, 1)), date(2026, 10, 14))

    def test_ows_time_rejection_400_falls_back_to_older_dekad(self):
        client = OwsClient(date(2026, 10, 11))
        payload, _, summary = cdi.collect(Channel, client, date(2026, 11, 5))
        self.assertEqual(summary["product_date"], "2026-10-11")
        self.assertEqual(len(client.urls), 3)
        self.assertEqual(payload, client.body)

    def test_other_400_answers_still_stop_the_run(self):
        other = (
            OWS_TIME_400.replace(b'locator="time"', b'locator="subset"'),
            OWS_TIME_400.replace(b"InvalidParameterValue", b"NoApplicableCode"),
            OWS_TIME_400.replace(b"ExceptionReport", b"Report"),
            b"",
            b"<html>Bad request</html>",
        )
        for body in other:
            client = OwsClient(date(2026, 10, 11), rejection=body)
            with self.subTest(body=body[:40]), self.assertRaises(FetchError) as caught:
                cdi.collect(Channel, client, date(2026, 11, 5))
            self.assertEqual(caught.exception.status, 400)
            self.assertEqual(len(client.urls), 1)

    def test_ows_time_report_with_other_status_is_not_trusted(self):
        for status in (404, 500, 503):
            client = OwsClient(date(2026, 10, 11), status=status)
            with self.subTest(status=status), self.assertRaises(FetchError):
                cdi.collect(Channel, client, date(2026, 11, 5))
            self.assertEqual(len(client.urls), 1)

    def test_transport_attaches_bounded_error_body_without_changing_message(self):
        url = cdi.request_url(date(2026, 11, 1))
        client = Client()
        body = OWS_TIME_400 + b"x" * 5000
        with (
            patch.object(client, "_policy", return_value=(None, 0)),
            patch.object(client, "_request", return_value=Response(400, body, {}, url)),
            self.assertRaises(FetchError) as caught,
        ):
            client.get(url)
        self.assertEqual(str(caught.exception), "Unexpected HTTP response")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.body, body[:2048])
        self.assertTrue(cdi.time_not_published(caught.exception))

    def test_changed_profile_is_refused_before_http(self):
        class Changed(Channel):
            config = {**cdi.CONFIG, "lon": [10, 30]}

        client = FakeClient(date(2026, 9, 11))
        with self.assertRaises(FetchError):
            cdi.collect(Changed, client, date(2026, 10, 14))
        self.assertEqual(client.urls, [])

    def test_only_reviewed_cdi_requests_get_longer_timeout(self):
        self.assertEqual(request_timeout(cdi.request_url(date(2026, 9, 11))), 60)
        self.assertEqual(request_timeout(cdi.BASE + "&TIME=2026-09-11"), 15)
        self.assertEqual(request_timeout("https://drought.emergency.copernicus.eu/"), 15)


class ChannelTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="t", sha256="c" * 64, sheets={})
        RegistryEntry.objects.create(
            registry_id=200, release=release, priority="P2", master={}, technical={}
        )

    def test_configure_is_dry_run_by_default_then_creates_disabled_and_keeps_state(self):
        call_command("configure_edo_cdi_raster", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        call_command("configure_edo_cdi_raster", "--apply", stdout=io.StringIO())
        channel = DataChannel.objects.get(slug=cdi.SLUG)
        self.assertFalse(channel.enabled or channel.access_approved)
        self.assertEqual((channel.interval_minutes, channel.region), (1440, "PL"))
        self.assertIn(
            "Copernicus Emergency Management Service information 2026", channel.attribution
        )
        out = io.StringIO()
        call_command("configure_edo_cdi_raster", "--apply", stdout=out)
        self.assertIn("unchanged", out.getvalue())
        DataChannel.objects.filter(pk=channel.pk).update(config={"mode": "other"})
        with self.assertRaises(CommandError):
            call_command("configure_edo_cdi_raster", "--apply", stdout=io.StringIO())

    def test_worker_run_stores_once_then_dedupes_identical_file(self):
        call_command("configure_edo_cdi_raster", "--apply", stdout=io.StringIO())
        DataChannel.objects.filter(slug=cdi.SLUG).update(enabled=True, access_approved=True)
        client = FakeClient(date(2026, 9, 11))
        with patch("ingestion.data_service.Client", return_value=client):
            first = run_data(cdi.SLUG, force=True)
            second = run_data(cdi.SLUG, force=True)
        self.assertEqual([first[0]["status"], second[0]["status"]], ["stored", "unchanged"])
        snapshot = DataSnapshot.objects.get()
        self.assertEqual(snapshot.media_type, "image/tiff")
        self.assertEqual(snapshot.summary["product_date"], "2026-09-11")
        self.assertGreater((snapshot.expires_at - snapshot.fetched_at).days, 28)

    def test_verify_command_compares_stored_copies_without_writes(self):
        call_command("configure_edo_cdi_raster", "--apply", stdout=io.StringIO())
        DataChannel.objects.filter(slug=cdi.SLUG).update(enabled=True, access_approved=True)
        with patch("ingestion.data_service.Client", return_value=FakeClient(date(2026, 9, 11))):
            run_data(cdi.SLUG, force=True)
        out = io.StringIO()
        call_command("verify_raster_copies", stdout=out)
        self.assertIn("Checked 1 copies; different 0.", out.getvalue())
        copy = DataSnapshot.objects.get()
        copy.summary["pixels_sha256"] = "0" * 64
        copy.save(update_fields=["summary"])
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("verify_raster_copies", stdout=out)
        self.assertIn(f"copy {copy.pk}: differs in pixels_sha256", out.getvalue())
        self.assertEqual(DataSnapshot.objects.count(), 1)

    def test_same_pixels_without_geokeys_is_not_stored_again(self):
        call_command("configure_edo_cdi_raster", "--apply", stdout=io.StringIO())
        DataChannel.objects.filter(slug=cdi.SLUG).update(enabled=True, access_approved=True)
        with_crs, without_crs = tiff(), tiff(crs=False)
        self.assertNotEqual(with_crs, without_crs)
        client = FakeClient(date(2026, 9, 11), bodies=[with_crs, without_crs, tiff(values=(0, 3))])
        with patch("ingestion.data_service.Client", return_value=client):
            statuses = [run_data(cdi.SLUG, force=True)[0]["status"] for _ in range(3)]
        self.assertEqual(statuses, ["stored", "unchanged", "stored"])
        first = DataSnapshot.objects.order_by("pk").first()
        self.assertEqual(first.payload, with_crs)
        self.assertEqual(DataSnapshot.objects.count(), 2)

    def test_missing_geokeys_are_warned_and_date_is_part_of_content_key(self):
        _, _, plain = cdi.collect(Channel, FakeClient(date(2026, 9, 11)), date(2026, 10, 14))
        _, _, bare = cdi.collect(
            Channel, FakeClient(date(2026, 9, 11), tiff(crs=False)), date(2026, 10, 14)
        )
        _, _, later = cdi.collect(Channel, FakeClient(date(2026, 9, 21)), date(2026, 10, 14))
        self.assertEqual(plain["content_sha256"], bare["content_sha256"])
        self.assertNotEqual(plain["sha256"], bare["sha256"])
        self.assertNotEqual(plain["content_sha256"], later["content_sha256"])
        self.assertIn("crs_missing_in_file_epsg4326_requested", bare["warnings"])
        self.assertNotIn("crs_missing_in_file_epsg4326_requested", plain["warnings"])

    def test_download_keeps_original_bytes_with_tif_suffix(self):
        call_command("configure_edo_cdi_raster", "--apply", stdout=io.StringIO())
        DataChannel.objects.filter(slug=cdi.SLUG).update(enabled=True, access_approved=True)
        body = tiff()
        with patch(
            "ingestion.data_service.Client", return_value=FakeClient(date(2026, 9, 11), body)
        ):
            run_data(cdi.SLUG, force=True)
        snapshot = DataSnapshot.objects.get()
        response = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, body)
        self.assertIn(f"geoklimat-{snapshot.pk}.tif", response["Content-Disposition"])
