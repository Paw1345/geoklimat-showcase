import csv
import io
import json
import zipfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from ingestion.data_retention import prune_cache, retained_snapshot
from ingestion.data_service import collect, run_data, store_snapshot
from ingestion.imgw_daily import COLUMNS, CONFIG, ROOT, collect_daily, parse_archive
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.observations import daily_anchor, daily_window, filter_observations
from ingestion.transport import FetchError, Response

NOW = datetime(2026, 9, 24, 10, tzinfo=UTC)


def row(day="26", station="349190600", **changes):
    fields = dict.fromkeys(COLUMNS, "")
    fields.update(NSP=station, POST="BIELSKO-BIAŁA", ROK="2026", MC="08", DZ=day)
    fields.update(TMAX="32.4", TMIN="0.0", STD="", WSTD="8", SMDB="", WSMDB="9")
    fields.update(changes)
    return [fields[column] for column in COLUMNS]


def archive(rows=None, name="s_d_08_2026.csv", extra=False):
    csvfile = io.StringIO()
    csv.writer(csvfile).writerows(rows if rows is not None else [row()])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(name, csvfile.getvalue().encode("cp1250"))
        if extra:
            zipped.writestr("unexpected.txt", "not executed")
    return out.getvalue()


def listing(path, links):
    return (
        "<h1>Index of " + path + "</h1>" + "".join(f'<a href="{link}">x</a>' for link in links)
    ).encode()


def fake_client(rows=None):
    base = ROOT.removeprefix("https://danepubliczne.imgw.pl").rstrip("/")
    bodies = {
        ROOT + "s_d_nag%C5%82%C3%B3wek.csv": (",".join(COLUMNS) + "\r\n").encode(),
        ROOT: listing(base, ["2025/", "2026/"]),
        ROOT + "2026/": listing(base + "/2026", ["2026_07_s.zip", "2026_08_s.zip"]),
        ROOT + "2026/2026_08_s.zip": archive(rows),
    }
    client = Mock()
    client.get.side_effect = lambda url: Response(200, bodies[url], {}, url)
    return client, bodies


class DailyParserTests(SimpleTestCase):
    def channel(self):
        return DataChannel(kind="imgw_daily", url=ROOT, region="PL", config=deepcopy(CONFIG))

    def test_all_columns_precision_and_missing_statuses_preserved(self):
        rows, digest = parse_archive(archive(), 2026, 8)
        self.assertEqual(len(rows[0]), 66)
        self.assertEqual(rows[0]["NSP"], "349190600")
        self.assertEqual(rows[0]["date"], "2026-08-26")
        self.assertEqual(rows[0]["POST"], "BIELSKO-BIAŁA")
        self.assertEqual((rows[0]["TMIN"], rows[0]["STD"], rows[0]["WSTD"]), ("0.0", "", "8"))
        self.assertEqual((rows[0]["SMDB"], rows[0]["WSMDB"]), ("", "9"))
        self.assertEqual(len(digest), 64)

    def test_zip_budgets_and_member_scope(self):
        for body in (
            b"not a zip",
            archive(name="../s_d_08_2026.csv"),
            archive(extra=True),
            archive([]),
        ):
            with self.subTest(body=body[:10]), self.assertRaises(FetchError):
                parse_archive(body, 2026, 8)
        with patch("ingestion.imgw_daily.MAX_UNPACKED_BYTES", 20), self.assertRaises(FetchError):
            parse_archive(archive(), 2026, 8)

    def test_duplicate_schema_month_and_numeric_rejections(self):
        cases = [
            [row(), row()],
            [row()[:-1]],
            [row("32")],
            [row(MC="09")],
            [row(station="bad")],
            [row(TMAX="NaN")],
            [row(WSTD="7")],
            [row(DZPS="2")],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(FetchError):
                parse_archive(archive(rows), 2026, 8)
        with patch("ingestion.imgw_daily.MAX_ROWS", 1), self.assertRaises(FetchError):
            parse_archive(archive([row(), row("27")]), 2026, 8)

    def test_collect_only_needed_published_months_and_recent_rows(self):
        client, _ = fake_client([row("01"), row("25"), row("26"), row("31")])
        payload, media, summary = collect_daily(self.channel(), client, NOW)
        data = json.loads(payload)
        self.assertEqual([r["date"] for r in data["records"]], ["2026-08-26", "2026-08-31"])
        self.assertEqual(data["unpublished_months"], ["2026-09"])
        self.assertEqual(len(data["coverage"]["expected_dates"]), 29)
        self.assertEqual(len(data["coverage"]["missing_dates"]), 27)
        self.assertEqual(data["units"]["SMDB"], "mm")
        self.assertEqual(summary["records"], 2)
        self.assertEqual(media, "application/json")
        self.assertEqual(client.get.call_count, 4)
        self.assertNotIn("2026_07_s.zip", " ".join(c.args[0] for c in client.get.call_args_list))

    def test_pinned_profile_responses_header_and_directory(self):
        channel = self.channel()
        channel.region = "EU"
        client, bodies = fake_client()
        with self.assertRaises(FetchError):
            collect_daily(channel, client, NOW)
        client.get.assert_not_called()
        for key, body in (
            (ROOT, b"<h1>Login</h1>"),
            (ROOT + "s_d_nag%C5%82%C3%B3wek.csv", b"NSP,POST,changed"),
            (ROOT, listing(ROOT, ["2026/", "2026/"])),
        ):
            client, bodies = fake_client()
            bodies[key] = body
            with self.assertRaises(FetchError):
                collect_daily(self.channel(), client, NOW)
        client.get.side_effect = None
        client.get.return_value = Response(200, b"", {}, "https://example.org/")
        with self.assertRaises(FetchError):
            collect_daily(self.channel(), client, NOW)

    def test_archive_not_published_is_visible_without_guessing_url(self):
        client, bodies = fake_client()
        bodies[ROOT] = listing(ROOT.removeprefix("https://danepubliczne.imgw.pl").rstrip("/"), [])
        payload, _, summary = collect_daily(self.channel(), client, NOW)
        self.assertEqual(summary["records"], 0)
        self.assertEqual(json.loads(payload)["unpublished_months"], ["2026-08", "2026-09"])
        self.assertEqual(client.get.call_count, 2)
        self.assertTrue(summary["partial"])

    def test_daily_boundary_dst_leap_and_year_change(self):
        for now in [
            NOW,
            datetime(2024, 3, 1, tzinfo=UTC),
            datetime(2026, 1, 5, tzinfo=UTC),
            datetime(2026, 3, 30, 10, tzinfo=UTC),
            datetime(2026, 10, 26, 10, tzinfo=UTC),
        ]:
            for day in daily_window(now):
                self.assertGreater(daily_anchor(day), now - timedelta(days=30))
                self.assertLess(daily_anchor(day), now)
        sample = {
            "records": [
                {"date": "2026-08-25"},
                {"date": "2026-08-26"},
                {"date": "2026-09-24"},
                {"date": "2026-09-25"},
            ]
        }
        kept, stamps = filter_observations(sample, "imgw_daily", NOW)
        self.assertEqual(len(sample["records"]), 4)
        self.assertEqual([r["date"] for r in kept["records"]], ["2026-08-26"])
        self.assertEqual(len(stamps), 1)


class DailyIntegrationTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="a" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=34, release=release, master={}, technical={}
        )

    def configured(self):
        call_command("configure_imgw_daily", apply=True, stdout=io.StringIO())
        return DataChannel.objects.get(slug="imgw-synop-daily")

    def snapshot(self):
        channel = self.configured()
        client, _ = fake_client([row("26"), row("31")])
        with patch("ingestion.data_service.timezone.now", return_value=NOW):
            payload, media, summary = collect(channel, client)
            snapshot, created = store_snapshot(channel, payload, media, summary)
        # Model field defaults retain their original callable, independent of the mocked clock.
        DataSnapshot.objects.filter(pk=snapshot.pk).update(fetched_at=NOW)
        snapshot.refresh_from_db()
        return channel, snapshot

    def test_configure_dry_repeat_and_preserve_admin(self):
        call_command("configure_imgw_daily", stdout=io.StringIO())
        self.assertFalse(DataChannel.objects.exists())
        channel = self.configured()
        self.assertFalse(channel.enabled or channel.access_approved)
        channel.enabled = channel.access_approved = True
        channel.name = "Administrator title"
        channel.save()
        call_command("configure_imgw_daily", apply=True, stdout=io.StringIO())
        channel.refresh_from_db()
        self.assertTrue(channel.enabled)
        self.assertEqual(channel.name, "Administrator title")
        channel.config = {"different": True}
        channel.save()
        with self.assertRaises(CommandError):
            call_command("configure_imgw_daily", apply=True, stdout=io.StringIO())

    def test_dry_run_import_repeat_and_quota(self):
        channel = self.configured()
        channel.enabled = channel.access_approved = True
        channel.save()
        client, _ = fake_client()
        with (
            patch("ingestion.data_service.Client", return_value=client),
            patch("ingestion.data_service.timezone.now", return_value=NOW),
        ):
            self.assertEqual(run_data(channel.slug, dry_run=True)[0]["status"], "dry_run")
            self.assertFalse(DataSnapshot.objects.exists())
            self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "stored")
            self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "unchanged")
            self.assertEqual(DataSnapshot.objects.count(), 1)
            with override_settings(DATA_STORAGE_MAX_BYTES=1), self.assertRaises(FetchError):
                store_snapshot(channel, b"new data", "application/json", {})

    def test_failed_archive_no_partial_snapshot(self):
        channel = self.configured()
        channel.enabled = channel.access_approved = True
        channel.save()
        client, bodies = fake_client()
        bodies[ROOT + "2026/2026_08_s.zip"] = b"bad zip"
        with (
            patch("ingestion.data_service.Client", return_value=client),
            patch("ingestion.data_service.timezone.now", return_value=NOW),
        ):
            self.assertEqual(run_data(channel.slug, force=True)[0]["status"], "failed")
        self.assertFalse(DataSnapshot.objects.exists())

    def test_export_and_prune_per_day_without_extending_fetch_time(self):
        channel, snapshot = self.snapshot()
        later = NOW + timedelta(days=1)
        original_payload = bytes(snapshot.payload)
        payload, summary, expires = retained_snapshot(snapshot, later)
        self.assertEqual([row["date"] for row in json.loads(payload)["records"]], ["2026-08-31"])
        self.assertEqual(summary["records"], 1)
        self.assertEqual(summary["coverage"]["available_dates"], ["2026-08-31"])
        self.assertLessEqual(expires, snapshot.fetched_at + timedelta(days=30))
        with patch("core.data_views.timezone.now", return_value=later):
            response = self.client.get(f"/data/snapshots/{snapshot.pk}/download/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, payload)
            self.assertContains(self.client.get(f"/data/{channel.pk}/"), "Zakres archiwum dobowego")
        snapshot.refresh_from_db()
        self.assertEqual(bytes(snapshot.payload), original_payload)
        with patch("ingestion.data_retention.timezone.now", return_value=later):
            self.assertEqual(prune_cache()["trimmed"], 1)
            self.assertEqual(prune_cache(apply=True)["trimmed"], 1)
            self.assertEqual(prune_cache(apply=True)["trimmed"], 0)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.fetched_at, NOW)
        self.assertEqual(bytes(snapshot.payload), payload)

    def test_empty_inventory_has_no_fictional_measurements(self):
        channel = self.configured()
        client, _ = fake_client([row("01")])
        with patch("ingestion.data_service.timezone.now", return_value=NOW):
            payload, media, summary = collect(channel, client)
            snapshot, _ = store_snapshot(channel, payload, media, summary)
        DataSnapshot.objects.filter(pk=snapshot.pk).update(fetched_at=NOW)
        snapshot.refresh_from_db()
        exported, summary, _ = retained_snapshot(snapshot, NOW + timedelta(days=1))
        self.assertEqual(json.loads(exported)["records"], [])
        self.assertEqual(summary["records"], 0)
        self.assertNotIn("oldest_observation", summary)
