import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ingestion.history_report import report
from ingestion.models import (
    CollectionAttempt,
    DataChannel,
    DataSnapshot,
    IngestionRun,
    RegistryEntry,
    RegistryRelease,
    Source,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
WARSAW = ZoneInfo("Europe/Warsaw")


class HistoryReportTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="a" * 64, sheets={})
        self.entry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.gios = self.channel("gios", "gios")
        self.stac = self.channel("stac", "stac", region="EU")
        self.wfs = self.channel("wfs", "geojson")
        self.source = Source.objects.create(
            slug="news", name="news", organization="o", url="https://example.org/", adapter="feed"
        )
        self.worker(*range(0, 30), 29.9)

    def worker(self, *days_ago):
        # News runs are a legacy proxy, not proof of completed data collection.
        for days in days_ago:
            IngestionRun.objects.create(source=self.source, started_at=NOW - timedelta(days=days))

    def channel(self, slug, kind, region="PL"):
        return DataChannel.objects.create(
            registry=self.entry,
            slug=slug,
            name=slug,
            kind=kind,
            region=region,
            url="https://example.org/",
            config={"secret": "never-export-this"},
        )

    def copy(self, channel, data, fetched_at=NOW, sha256=None, **summary):
        payload = json.dumps(data).encode()
        return DataSnapshot.objects.create(
            channel=channel,
            payload=payload,
            size_bytes=len(payload),
            sha256=sha256 or hashlib.sha256(payload).hexdigest(),
            media_type="application/json",
            fetched_at=fetched_at,
            expires_at=fetched_at + timedelta(days=30),
            summary={"kind": channel.kind, **summary},
        )

    def gios_rows(self, *days_ago):
        rows = []
        for days in days_ago:
            stamp = (NOW - timedelta(days=days)).astimezone(WARSAW)
            rows.append({"Data": stamp.strftime("%Y-%m-%d %H:%M:%S"), "Wartość": 1.0})
        return {"Lista danych pomiarowych": rows}

    def series(self, result, slug):
        return next(row for row in result["series"] if row["slug"] == slug)

    def test_counts_observation_days_and_reports_local_gaps(self):
        self.copy(
            self.gios, self.gios_rows(*range(0, 30), 29.9), observation_timezone="Europe/Warsaw"
        )
        self.copy(self.gios, self.gios_rows(0, 1), observation_timezone="Europe/Warsaw")
        result = report(NOW)
        gios = self.series(result, "gios")
        self.assertEqual(gios["copies_scanned"], 2)
        self.assertEqual(gios["day_zone"], "Europe/Warsaw")
        self.assertEqual(gios["observation_occurrences_by_day"]["2026-10-07"], 2)
        self.assertEqual(gios["days_without_observations"], [])
        self.assertTrue(gios["full_window_observed"])
        self.assertTrue(gios["gap_cause_known"])
        self.assertEqual(gios["days_source_unpublished"], [])
        self.assertEqual(gios["partial_days"], ["2026-09-07", "2026-10-07"])
        stac = self.series(result, "stac")
        self.assertEqual(stac["copies_scanned"], 0)
        self.assertFalse(stac["full_window_observed"])
        self.assertEqual(len(stac["days_without_observations"]), 31)
        self.assertFalse(stac["gap_cause_known"])
        self.assertEqual(result["series_with_gaps"], 1)
        self.assertFalse(result["agreed_series_confirmed"])
        self.assertFalse(result["source_gaps_documented"])
        self.assertNotIn("never-export-this", json.dumps(result, default=str))

    def test_missing_days_and_observations_older_than_window(self):
        self.copy(self.gios, self.gios_rows(0, 2, 31), observation_timezone="Europe/Warsaw")
        gios = self.series(report(NOW), "gios")
        self.assertIn("2026-10-06", gios["days_without_observations"])
        self.assertNotIn("2026-10-05", gios["days_without_observations"])
        self.assertIn("2026-10-06", gios["days_gap_unexplained"])
        self.assertFalse(gios["gap_cause_known"])
        self.assertEqual(sum(gios["observation_occurrences_by_day"].values()), 2)
        self.assertEqual(gios["oldest_observation"], (NOW - timedelta(days=2)).replace(tzinfo=UTC))

    def test_day_without_completed_worker_cycle_is_a_known_local_gap(self):
        IngestionRun.objects.filter(
            started_at__in=[NOW - timedelta(days=days) for days in (3, 4)]
        ).delete()
        self.copy(self.gios, self.gios_rows(0, 1, 2, 5), observation_timezone="Europe/Warsaw")
        result = report(NOW)
        gios = self.series(result, "gios")
        self.assertEqual(gios["days_no_collection_cycle"], ["2026-10-03", "2026-10-04"])
        self.assertNotIn("2026-10-03", gios["days_gap_unexplained"])
        self.assertIn("2026-10-01", gios["days_gap_unexplained"])
        self.assertEqual(result["worker_active_days"]["Europe/Warsaw"], 29)
        IngestionRun.objects.all().delete()
        gios = self.series(report(NOW), "gios")
        self.assertEqual(gios["days_gap_unexplained"], [])
        self.assertTrue(gios["gap_cause_known"])
        self.assertFalse(gios["full_window_observed"])

    def test_imgw_daily_unpublished_month_is_a_source_gap(self):
        daily = self.channel("daily", "imgw_daily")
        days = [f"2026-09-{d:02d}" for d in range(1, 31)]
        data = {"records": [{"date": day, "NSP": "1"} for day in days]}
        self.copy(daily, data, unpublished_months=["2026-10"])
        row = self.series(report(NOW), "daily")
        self.assertEqual(row["days_not_expected"], ["2026-09-07", "2026-10-07"])
        october = [f"2026-10-{d:02d}" for d in range(1, 7)]
        self.assertEqual(row["days_source_unpublished"], october)
        self.assertEqual(row["days_gap_unexplained"], [])
        self.assertTrue(row["gap_cause_known"])
        self.assertFalse(row["full_window_observed"])
        older = self.copy(daily, data, fetched_at=NOW - timedelta(days=1))
        self.assertNotEqual(older.summary.get("unpublished_months"), ["2026-10"])
        self.assertEqual(self.series(report(NOW), "daily")["days_source_unpublished"], october)

    def test_imgw_gaps_remain_unknown_with_or_without_news_runs(self):
        for profile in ("synop", "meteo", "hydro"):
            channel = self.channel(f"imgw-{profile}", "imgw")
            self.copy(channel, [{"data_pomiaru": "2026-10-07", "godzina_pomiaru": "12"}])
        for has_news in (True, False):
            if not has_news:
                IngestionRun.objects.all().delete()
            result = report(NOW)
            for profile in ("synop", "meteo", "hydro"):
                with self.subTest(profile=profile, has_news=has_news):
                    row = self.series(result, f"imgw-{profile}")
                    self.assertEqual(row["days_no_collection_cycle"], [])
                    self.assertEqual(row["days_gap_unexplained"], row["days_without_observations"])
                    self.assertFalse(row["gap_cause_known"])
                    self.assertFalse(row["full_window_observed"])

    def test_news_only_report_explicitly_labels_proxy_not_worker_evidence(self):
        channel = self.channel("imgw-synop", "imgw")
        out = io.StringIO()
        call_command("history_report", channel=[channel.pk], stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual(result["worker_active_days_basis"], "news_ingestion_run_proxy")
        self.assertFalse(result["worker_continuity_confirmed"])
        self.assertFalse(result["series"][0]["collection_continuity_confirmed"])
        self.assertEqual(result["series"][0]["copies_scanned"], 0)
        self.assertNotIn("never-export-this", out.getvalue())

    def test_imgw_observation_day_is_not_the_collection_day(self):
        now = datetime(2026, 10, 12, 12, tzinfo=UTC)
        fetched = datetime(2026, 10, 11, 12, tzinfo=UTC)
        payloads = {
            "synop": {"data_pomiaru": "2026-10-10", "godzina_pomiaru": "12"},
            "meteo": {"temperatura_powietrza_data": "2026-10-10 12:00:00"},
            "hydro": {"stan_wody_data_pomiaru": "2026-10-10 12:00:00"},
        }
        for profile, payload in payloads.items():
            self.copy(self.channel(f"imgw-{profile}", "imgw"), [payload], fetched_at=fetched)
        result = report(now)
        for profile in payloads:
            with self.subTest(profile=profile):
                row = self.series(result, f"imgw-{profile}")
                self.assertEqual(row["observation_occurrences_by_day"]["2026-10-10"], 1)
                self.assertEqual(row["observation_occurrences_by_day"]["2026-10-11"], 0)
                self.assertFalse(row["collection_continuity_confirmed"])
                self.assertFalse(row["gap_cause_known"])

    def test_imgw_full_date_coverage_does_not_confirm_collection_continuity(self):
        channel = self.channel("imgw-synop", "imgw")
        stamps = [NOW - timedelta(days=days) for days in range(30)] + [NOW - timedelta(days=29.9)]
        self.copy(channel, [{"temperatura_powietrza_data": stamp.isoformat()} for stamp in stamps])
        IngestionRun.objects.all().delete()
        row = self.series(report(NOW), channel.slug)
        self.assertTrue(row["full_window_observed"])
        self.assertEqual(row["days_without_observations"], [])
        self.assertTrue(row["gap_cause_known"])
        self.assertFalse(row["collection_continuity_confirmed"])

    def test_stac_uses_utc_days(self):
        late = datetime(2026, 10, 6, 23, 30, tzinfo=UTC)
        data = {"features": [{"properties": {"datetime": late.isoformat()}}]}
        self.copy(self.stac, data, observation_timezone="UTC")
        stac = self.series(report(NOW), "stac")
        self.assertEqual(stac["day_zone"], "UTC")
        self.assertEqual(stac["observation_occurrences_by_day"]["2026-10-06"], 1)
        self.assertEqual(stac["observation_occurrences_by_day"]["2026-10-07"], 0)

    def test_unverified_expired_and_static_copies(self):
        self.copy(self.gios, self.gios_rows(0), sha256="b" * 64)
        self.copy(self.gios, self.gios_rows(0), fetched_at=NOW - timedelta(days=30))
        self.copy(self.wfs, {"type": "FeatureCollection", "features": []})
        result = report(NOW)
        gios = self.series(result, "gios")
        self.assertEqual(len(gios["unverified_copies"]), 1)
        self.assertEqual(gios["copies_scanned"], 0)
        self.assertFalse(gios["full_window_observed"])
        self.assertEqual(result["series_with_unverified_copies"], 1)
        static = result["static"][0]
        self.assertEqual((static["slug"], static["live_copies"]), ("wfs", 1))
        self.assertFalse(static["history_applicable"])

    @override_settings(DATA_STORAGE_MAX_BYTES=10)
    def test_quota_and_command_is_read_only(self):
        self.copy(self.gios, self.gios_rows(0))
        before = list(DataSnapshot.objects.values_list("pk", "sha256", "summary"))
        out = io.StringIO()
        call_command("history_report", channel=[self.gios.pk], stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual([row["slug"] for row in result["series"]], ["gios"])
        self.assertFalse(result["quota"]["within_quota"])
        self.assertGreater(result["quota"]["over_quota_bytes"], 0)
        self.assertEqual(before, list(DataSnapshot.objects.values_list("pk", "sha256", "summary")))

    def reviewed_imgw(self):
        channel = self.channel("imgw", "imgw")
        channel.url = "https://danepubliczne.imgw.pl/api/data/synop"
        channel.save(update_fields=["url"])
        return channel

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_report_exposes_worker_attempts_separately_from_observation_coverage(self):
        channel = self.reviewed_imgw()
        from ingestion.downloads import configuration

        CollectionAttempt.objects.create(
            channel=channel,
            attempt_id=uuid4(),
            batch_id=uuid4(),
            origin="worker",
            started_at=NOW - timedelta(minutes=10),
            finished_at=NOW - timedelta(minutes=9),
            due_at=NOW - timedelta(minutes=11),
            interval_minutes=60,
            profile_sha256=configuration(channel),
            outcome="unchanged",
            reason_code="",
        )
        result = report(NOW)
        self.assertIn("collection_evidence_status", result)
        self.assertEqual(result["collection_evidence_status"], "available")
        series = self.series(result, "imgw")
        detail = series["collection_evidence"]
        self.assertEqual(detail["by_origin"]["worker"]["outcomes"]["unchanged"], 1)
        self.assertEqual(detail["by_origin"]["worker"]["max_lateness_seconds"], 60)
        self.assertFalse(series["full_window_observed"])
        self.assertFalse(series["collection_continuity_confirmed"])
        self.assertFalse(result["worker_continuity_confirmed"])
        self.assertEqual(
            self.series(result, "gios")["collection_evidence"]["status"], "not_applicable"
        )
        self.assertNotIn("never-export-this", json.dumps(result, default=str))

    @override_settings(COLLECTION_EVIDENCE_ENABLED=False)
    def test_off_history_report_works_without_ledger_and_does_not_query_it(self):
        self.reviewed_imgw()
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_off_report"
            )
        try:
            with CaptureQueriesContext(connection) as queries:
                result = report(NOW)
            self.assertIn("collection_evidence_status", result)
            self.assertEqual(result["collection_evidence_status"], "disabled")
            self.assertEqual(
                self.series(result, "imgw")["collection_evidence"]["status"], "disabled"
            )
            self.assertFalse(any("ingestion_collectionattempt" in row["sql"] for row in queries))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_off_report RENAME TO ingestion_collectionattempt"
                )

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_on_history_report_missing_schema_preserves_observation_result(self):
        channel = self.reviewed_imgw()
        self.copy(channel, [{"data_pomiaru": "2026-10-07", "godzina_pomiaru": "12"}])
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ingestion_collectionattempt RENAME TO test_absent_on_report"
            )
        try:
            with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
                result = report(NOW)
            self.assertEqual(result["collection_evidence_status"], "unavailable")
            series = self.series(result, "imgw")
            self.assertEqual(series["copies_scanned"], 1)
            self.assertEqual(series["collection_evidence"]["status"], "unavailable")
            self.assertFalse(series["collection_continuity_confirmed"])
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE test_absent_on_report RENAME TO ingestion_collectionattempt"
                )

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_empty_or_capped_evidence_never_becomes_continuity_pass(self):
        channel = self.reviewed_imgw()
        result = report(NOW)
        self.assertIn("collection_evidence_status", result)
        self.assertEqual(
            self.series(result, "imgw")["collection_evidence"]["status"], "no_evidence"
        )
        from ingestion.downloads import configuration

        CollectionAttempt.objects.create(
            channel=channel,
            attempt_id=uuid4(),
            origin="unknown",
            interval_minutes=60,
            started_at=NOW - timedelta(minutes=1),
            finished_at=NOW,
            profile_sha256=configuration(channel),
            outcome="stored",
            reason_code="",
        )
        with patch("ingestion.collection_evidence.READ_ATTEMPT_LIMIT", 0):
            capped = report(NOW)
        self.assertEqual(capped["collection_evidence_status"], "capped")
        self.assertFalse(self.series(capped, "imgw")["collection_continuity_confirmed"])

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_invalid_profile_does_not_crash_unrelated_series(self):
        channel = self.reviewed_imgw()
        channel.config = ["never-export-this"]
        channel.save(update_fields=["config"])
        with self.assertLogs("ingestion.collection_evidence", level="WARNING"):
            result = report(NOW)
        self.assertEqual(result["collection_evidence_status"], "unavailable")
        self.assertEqual(
            self.series(result, "imgw")["collection_evidence"]["status"], "unavailable"
        )
        self.assertEqual(
            self.series(result, "gios")["collection_evidence"]["status"], "not_applicable"
        )
        self.assertNotIn("never-export-this", json.dumps(result, default=str))

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_named_window_is_additive_and_uses_one_bounded_ledger_select(self):
        from ingestion.collection_continuity import PILOT_START
        from ingestion.downloads import configuration

        channel = self.reviewed_imgw()
        CollectionAttempt.objects.create(
            channel=channel,
            attempt_id=uuid4(),
            batch_id=uuid4(),
            origin="worker",
            started_at=PILOT_START + timedelta(seconds=25),
            finished_at=PILOT_START + timedelta(seconds=26),
            due_at=PILOT_START - timedelta(hours=6),
            interval_minutes=60,
            profile_sha256=configuration(channel),
            outcome="stored",
            reason_code="",
        )
        now = PILOT_START + timedelta(minutes=2)
        with CaptureQueriesContext(connection) as queries:
            result = report(now, imgw_pilot_window=True)
        detail = result["collection_acceptance"]["by_channel"][channel.pk]
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(detail["boundary_carry_in_attempts"], 1)
        self.assertEqual(detail["tolerance_seconds"], 300)
        self.assertFalse(detail["continuity_confirmed"])
        self.assertFalse(result["worker_continuity_confirmed"])
        ledger_reads = [
            row["sql"]
            for row in queries
            if "SELECT" in row["sql"] and "ingestion_collectionattempt" in row["sql"]
        ]
        self.assertEqual(len(ledger_reads), 1)
        self.assertIn("LIMIT 10001", ledger_reads[0])
        self.assertNotIn("never-export-this", json.dumps(result, default=str))
        self.assertNotIn("collection_acceptance", report(now))

    @override_settings(COLLECTION_EVIDENCE_ENABLED=False)
    def test_named_window_off_keeps_zero_ledger_sql_and_no_pass(self):
        self.reviewed_imgw()
        with CaptureQueriesContext(connection) as queries:
            result = report(NOW, imgw_pilot_window=True)
        self.assertEqual(result["collection_acceptance"]["evidence_status"], "disabled")
        self.assertEqual(result["collection_acceptance"]["by_channel"], {})
        self.assertFalse(any("ingestion_collectionattempt" in row["sql"] for row in queries))

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_named_window_cli_is_explicit_and_serializable(self):
        output = io.StringIO()
        call_command("history_report", imgw_pilot_window=True, stdout=output)
        result = json.loads(output.getvalue())
        policy = result["collection_acceptance"]["policy"]
        self.assertEqual(policy["window_start"], "2026-10-15T08:00:00Z")
        self.assertEqual(policy["window_end"], "2026-11-14T08:00:00Z")
        self.assertEqual(policy["tolerance_seconds"], 300)

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_named_boundary_full_fixture_does_not_change_rolling_boundary(self):
        from ingestion.collection_continuity import PILOT_END, PILOT_START
        from ingestion.downloads import configuration

        channel = self.reviewed_imgw()
        fingerprint = configuration(channel)
        batch = uuid4()
        CollectionAttempt.objects.bulk_create(
            [
                CollectionAttempt(
                    channel=channel,
                    attempt_id=uuid4(),
                    batch_id=batch,
                    origin="worker",
                    started_at=PILOT_START + timedelta(hours=hour),
                    finished_at=PILOT_START + timedelta(hours=hour, seconds=1),
                    due_at=PILOT_START + timedelta(hours=hour),
                    interval_minutes=60,
                    profile_sha256=fingerprint,
                    outcome="unchanged",
                    reason_code="",
                )
                for hour in range(720)
            ]
        )
        result = report(PILOT_END, imgw_pilot_window=True)
        acceptance = result["collection_acceptance"]["by_channel"][channel.pk]
        self.assertEqual(acceptance["status"], "pass")
        self.assertEqual(acceptance["worker_attempts"], 720)
        self.assertTrue(acceptance["continuity_confirmed"])
        legacy = self.series(result, "imgw")["collection_evidence"]
        self.assertEqual(legacy["by_origin"]["worker"]["attempts"], 719)
        self.assertFalse(legacy["continuity_confirmed"])
        self.assertFalse(result["worker_continuity_confirmed"])
        self.assertEqual(result["collection_acceptance"]["metadata_retention_days"], 35)
        from ingestion.collection_evidence import prune_attempts

        for days in (1, 5):
            with self.subTest(review_margin_days=days):
                as_of = PILOT_END + timedelta(days=days)
                self.assertEqual(prune_attempts(apply=True, now=as_of)["deleted"], 0)
                delayed = report(as_of, imgw_pilot_window=True)
                self.assertEqual(delayed["retention_days"], 30)
                self.assertEqual(delayed["collection_acceptance"]["metadata_retention_days"], 35)
                accepted = delayed["collection_acceptance"]["by_channel"][channel.pk]
                self.assertEqual((accepted["status"], accepted["worker_attempts"]), ("pass", 720))
        with override_settings(DATA_RETENTION_DAYS=1):
            short_cache = report(PILOT_END + timedelta(days=1), imgw_pilot_window=True)
        self.assertEqual(short_cache["retention_days"], 1)
        self.assertEqual(
            short_cache["collection_acceptance"]["by_channel"][channel.pk]["status"], "pass"
        )
        expired = report(PILOT_END + timedelta(days=5, microseconds=1), imgw_pilot_window=True)
        self.assertEqual(
            expired["collection_acceptance"]["by_channel"][channel.pk]["status"], "unconfirmed"
        )
        with patch("ingestion.collection_evidence.READ_ATTEMPT_LIMIT", 1):
            capped = report(PILOT_END, imgw_pilot_window=True)
        self.assertEqual(capped["collection_acceptance"]["evidence_status"], "capped")
        self.assertEqual(capped["collection_acceptance"]["by_channel"], {})

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_named_reader_excludes_rows_outside_metadata_retention_before_cap(self):
        from ingestion.collection_continuity import PILOT_END, PILOT_START
        from ingestion.downloads import configuration

        channel = self.reviewed_imgw()
        now = PILOT_END + timedelta(days=6)
        for stamp in (PILOT_START + timedelta(hours=1), now - timedelta(minutes=10)):
            CollectionAttempt.objects.create(
                channel=channel,
                attempt_id=uuid4(),
                batch_id=uuid4(),
                origin="worker",
                started_at=stamp,
                finished_at=stamp + timedelta(seconds=1),
                due_at=stamp,
                interval_minutes=60,
                profile_sha256=configuration(channel),
                outcome="stored",
                reason_code="",
            )
        with patch("ingestion.collection_evidence.READ_ATTEMPT_LIMIT", 1):
            result = report(now, imgw_pilot_window=True)
        self.assertEqual(result["collection_acceptance"]["evidence_status"], "available")
        accepted = result["collection_acceptance"]["by_channel"][channel.pk]
        self.assertEqual(accepted["status"], "unconfirmed")
        self.assertIn("window_start_not_retained", accepted["reasons"])

    def test_named_window_refuses_non_boolean_choice_before_sql(self):
        with self.assertNumQueries(0), self.assertRaises(ValueError):
            report(NOW, imgw_pilot_window="yes")


class NamedContinuityWindowTests(SimpleTestCase):
    start = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
    end = start + timedelta(days=30)

    def row(self, hours=0, **changes):
        stamp = self.start + timedelta(hours=hours)
        return {
            "origin": "worker",
            "started_at": stamp,
            "finished_at": stamp + timedelta(seconds=1),
            "due_at": stamp,
            "interval_minutes": 60,
            "profile_sha256": "a" * 64,
            "outcome": "stored",
        } | changes

    def assess(self, rows, now=None, **changes):
        from ingestion.collection_continuity import assess_pilot_window

        return assess_pilot_window(
            rows,
            now=now or self.end,
            current_profile="a" * 64,
            current_interval=60,
            retained_start=self.start,
            **changes,
        )

    def complete_rows(self):
        return [self.row(hour) for hour in range(720)]

    def test_real_full_window_required_and_utc_duration_crosses_dst(self):
        rows = self.complete_rows()
        result = self.assess(rows)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["continuity_confirmed"])
        self.assertEqual(result["window_end"].astimezone(WARSAW).hour, 9)
        self.assertEqual(result["window_start"].astimezone(WARSAW).hour, 10)
        partial = self.assess(rows, now=self.end - timedelta(microseconds=1))
        self.assertEqual(partial["status"], "pending")
        self.assertFalse(partial["continuity_confirmed"])

    def test_five_minute_lateness_is_inclusive_but_one_microsecond_more_fails(self):
        for lag, expected in ((300, "pass"), (300.000001, "failed")):
            with self.subTest(lag=lag):
                rows = self.complete_rows()
                rows[5]["due_at"] -= timedelta(seconds=lag)
                result = self.assess(rows)
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["late_attempts"], int(expected == "failed"))

    def test_gap_five_minutes_is_inclusive_but_missing_worker_cycle_fails(self):
        for excess, expected in ((300, "pass"), (300.000001, "failed")):
            with self.subTest(excess=excess):
                rows = self.complete_rows()
                rows[5] = self.row(5 + excess / 3600)
                result = self.assess(rows)
                self.assertEqual(result["status"], expected)
        rows = self.complete_rows()
        rows[5]["origin"] = "manual"
        rows[6]["origin"] = "unknown"
        result = self.assess(rows)
        self.assertEqual(result["status"], "failed")
        self.assertIn("worker_gap_over_tolerance", result["reasons"])

    def test_missing_leading_or_trailing_worker_proof_is_not_pass(self):
        for rows in (self.complete_rows()[2:], self.complete_rows()[:-2]):
            with self.subTest(first=rows[0]["started_at"]):
                result = self.assess(rows)
                self.assertEqual(result["status"], "failed")
                self.assertIn("window_edge_gap", result["reasons"])

    def test_failed_busy_changed_profile_interval_due_and_early_start_block_pass(self):
        for changes in (
            {"outcome": "failed"},
            {"outcome": "busy"},
            {"profile_sha256": "b" * 64},
            {"interval_minutes": 30},
            {"due_at": None},
            {"due_at": self.start + timedelta(hours=5, seconds=1)},
        ):
            with self.subTest(changes=changes):
                rows = self.complete_rows()
                rows[5].update(changes)
                result = self.assess(rows)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["continuity_confirmed"])

    def test_first_overdue_boundary_is_visible_but_historical_gap_not_rejudged(self):
        rows = self.complete_rows()
        rows[0]["due_at"] = self.start - timedelta(hours=6)
        result = self.assess([self.row(-7, outcome="failed"), *rows])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["boundary_carry_in_attempts"], 1)
        self.assertEqual(result["max_lateness_seconds"], 0)
        rows[1]["due_at"] = self.start - timedelta(seconds=1)
        self.assertEqual(self.assess(rows)["status"], "failed")

    def test_no_worker_future_and_unfinished_proof_are_unconfirmed(self):
        for rows in (
            [],
            [self.row(origin="manual")],
            [self.row(origin="unknown")],
            [self.row(721)],
            [self.row(finished_at=self.end + timedelta(seconds=1))],
        ):
            with self.subTest(rows=rows):
                result = self.assess(rows)
                self.assertEqual(result["status"], "unconfirmed")
                self.assertFalse(result["continuity_confirmed"])

    def test_retention_loss_refuses_full_window_even_with_complete_fixture(self):
        from ingestion.collection_continuity import assess_pilot_window

        result = assess_pilot_window(
            self.complete_rows(),
            now=self.end + timedelta(seconds=1),
            current_profile="a" * 64,
            current_interval=60,
            retained_start=self.start + timedelta(seconds=1),
        )
        self.assertEqual(result["status"], "unconfirmed")
        self.assertIn("window_start_not_retained", result["reasons"])
        self.assertFalse(result["continuity_confirmed"])

    def test_before_window_is_pending_and_naive_time_is_rejected(self):
        self.assertEqual(
            self.assess([], now=self.start - timedelta(seconds=1))["status"], "pending"
        )
        with self.assertRaises(ValueError):
            self.assess([], now=self.end.replace(tzinfo=None))
