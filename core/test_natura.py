import io
import os
from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError, connection, transaction
from django.test import RequestFactory, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core import natura
from core.natura_runner import ReportRefused
from ingestion import eea_natura_file as profile
from ingestion.downloads import CHUNK_BYTES, configuration
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.natura_sites import FIELDS, SHA256, parse_sites
from ingestion.test_natura_sites import fixture, site


class NaturaCopySelectionTests(TransactionTestCase):
    def test_http_selected_copy_renders_verified_report_and_escaped_examples(self):
        job, summary = self.report_copy([site(LATITUDE="<script>alert(1)</script>")])
        with patch("core.natura_runner.run_report", return_value=summary) as child:
            response = self.headers(self.client.get(self.url, {"job": job.pk}), 200)
        child.assert_called_once_with(job.pk)
        self.assertEqual(response.context["summary"], summary)
        self.assertContains(response, "Struktura: PASS")
        self.assertContains(response, "Jakość: WARN")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")
        self.assertNotContains(response, "Tylko metadane")
        self.assertNotContains(response, 'http-equiv="refresh"')

    def test_http_refusals_keep_headers_and_never_render_partial_report(self):
        statuses = {"missing": 404, "invalid": 409, "busy": 503, "timeout": 503, "unavailable": 503}
        for code, status in statuses.items():
            with (
                self.subTest(code=code),
                patch("core.natura.verified_report", side_effect=ReportRefused(code)),
            ):
                response = self.headers(self.client.get(self.url, {"job": self.job.pk}), status)
            self.assertNotIn("summary", response.context)
            self.assertNotContains(response, "Struktura: PASS", status_code=status)
            if status == 503:
                self.assertEqual(response["Retry-After"], "5")
            else:
                self.assertNotIn("Retry-After", response)

    def test_http_list_offers_explicit_get_buttons_without_starting_reports(self):
        with patch("core.natura.verified_report", side_effect=AssertionError("No report")):
            response = self.headers(self.client.get(self.url), 200)
        self.assertContains(response, 'method="get"')
        self.assertContains(response, 'name="job"')
        self.assertContains(response, "Oblicz raport kopii nr")
        self.assertContains(response, "Tylko metadane")

    def test_natura_data_detail_link_needs_staff_approval_and_exact_profile(self):
        url = reverse("data_detail", args=[self.channel.pk])
        link = reverse("natura_report", args=[self.channel.pk])
        self.assertContains(self.client.get(url), 'href="' + link + '"')
        fields = {
            "config": {},
            "url": "https://invalid.test/",
            "kind": "table",
            "region": "PL",
            "access_approved": False,
            "slug": "other",
        }
        for field, value in fields.items():
            with self.subTest(field=field):
                original = getattr(self.channel, field)
                DataChannel.objects.filter(pk=self.channel.pk).update(**{field: value})
                self.assertNotContains(self.client.get(url), 'href="' + link + '"')
                DataChannel.objects.filter(pk=self.channel.pk).update(**{field: original})
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        self.assertNotContains(self.client.get(url), 'href="' + link + '"')
        self.client.logout()
        self.assertNotContains(self.client.get(url), 'href="' + link + '"')

    def report_copy(self, rows=None):
        job = self.make_copy()
        chunks = []
        for offset in range(0, profile.SIZE, CHUNK_BYTES):
            chunks.append(
                DataSnapshot(
                    channel=self.channel,
                    download_job=job,
                    payload=b"x",
                    size_bytes=min(CHUNK_BYTES, profile.SIZE - offset),
                    sha256="1" * 64,
                    expires_at=job.expires_at,
                    media_type="application/octet-stream",
                    summary={"kind": "file_chunk", "scope": "EU", "offset": offset},
                )
            )
        DataSnapshot.objects.bulk_create(chunks)
        summary = parse_sites(io.BytesIO(fixture(rows)))
        summary.update(
            job_id=job.pk,
            channel_id=self.channel.pk,
            source_sha256=SHA256,
            source_url=profile.URL,
            source_bytes=profile.SIZE,
            attribution=self.channel.attribution,
            license="CC BY 4.0 (DG ENV)",
            dataset_uuid=profile.CONFIG["dataset_uuid"],
            copied_at=job.created_at.isoformat(),
            expires_at=job.expires_at.isoformat(),
            retention_basis="local_copy_age",
        )
        # Deliberately invalid payload: only stubbed child output can succeed here.
        return job, summary

    def test_verified_result_uses_fresh_snapshots_without_parent_payload_reads(self):
        job, summary = self.report_copy()

        def child(job_id):
            self.assertEqual(job_id, job.pk)
            self.assertFalse(connection.in_atomic_block)
            return deepcopy(summary)

        with (
            patch("core.natura_runner.run_report", side_effect=child),
            CaptureQueriesContext(connection) as queries,
        ):
            result = natura.verified_report(self.channel.pk, job.pk)
        self.assertEqual(result["summary"], summary)
        self.assertEqual(result["selected"]["id"], job.pk)
        self.assertNotIn("fingerprint", result)
        sql = " ".join(q["sql"] for q in queries)
        self.assertEqual(sql.count("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"), 2)
        self.assertNotIn("payload", sql.lower())
        self.assertFalse(
            any(
                q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )

    def test_verified_result_refuses_withdrawn_approval_after_child(self):
        job, summary = self.report_copy()

        def child(job_id):
            DataChannel.objects.filter(pk=self.channel.pk).update(access_approved=False)
            return summary

        with (
            patch("core.natura_runner.run_report", side_effect=child),
            self.assertRaises(ReportRefused) as error,
        ):
            natura.verified_report(self.channel.pk, job.pk)
        self.assertEqual(error.exception.code, "invalid")

    def test_verified_result_missing_copy_never_starts_child(self):
        with (
            patch("core.natura_runner.run_report") as child,
            self.assertRaises(ReportRefused) as error,
        ):
            natura.verified_report(self.channel.pk, 2**63 - 1)
        self.assertEqual(error.exception.code, "missing")
        child.assert_not_called()

    def test_real_child_cli_is_isolated_and_rejects_invalid_test_payload(self):
        job, _ = self.report_copy()
        database = connection.settings_dict["NAME"]
        self.assertTrue(database.startswith("test_"), "Child must never use the live DB")
        before = list(job.chunks.values_list("pk", "sha256", "size_bytes"))
        with (
            patch.dict(os.environ, {"POSTGRES_DB": database}),
            self.assertRaises(ReportRefused) as error,
        ):
            natura.verified_report(self.channel.pk, job.pk)
        self.assertEqual(error.exception.code, "invalid")
        self.assertEqual(list(job.chunks.values_list("pk", "sha256", "size_bytes")), before)

    def test_verified_result_rejects_wrong_provenance_and_schema(self):
        job, summary = self.report_copy()
        changes = {
            "job_id": True,
            "channel_id": self.channel.pk + 1,
            "source_sha256": "0" * 64,
            "source_bytes": True,
            "source_url": "https://invalid.test/",
            "attribution": "other",
            "copied_at": "2021-01-01",
            "expires_at": "2099-01-01",
            "license": "unconfirmed",
            "release": "current",
            "dataset_uuid": "other",
            "retention_basis": "observation_age",
            "structure_verified": 1,
            "current_registry_confirmed": True,
            "geometry_included": True,
            "national_completeness_confirmed": True,
            "history_30_days_applicable": True,
            "coordinate_reference": "unknown",
            "date_precision": "datetime",
            "records": True,
            "unique_sitecodes": 0,
            "poland_records": 2,
            "fields": ["SITECODE"],
            "max_field_chars": 1024 * 1024 + 1,
            "country_counts": {"PL": 2},
            "country_counts_raw": {"XX": 1},
            "type_counts_raw": {"B": -1},
            "blank_counts": {"OTHER": 1},
            "quality_counts": {"SITECODE_unknown": 1},
            "quality_status": "pass",
            "quality_examples": [{"sitecode": "PL1", "field": "OTHER", "raw": "x"}],
            "date_ranges": {"DATE_COMPILATION": {"min_raw": "bad"}},
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                broken = {**summary, field: value}
                with (
                    patch("core.natura_runner.run_report", return_value=broken),
                    self.assertRaises(ReportRefused) as error,
                ):
                    natura.verified_report(self.channel.pk, job.pk)
                self.assertEqual(error.exception.code, "invalid")
        for broken in (
            [],
            {**summary, "new_field": 1},
            {k: v for k, v in summary.items() if k != "records"},
        ):
            with (
                patch("core.natura_runner.run_report", return_value=broken),
                self.assertRaises(ReportRefused) as error,
            ):
                natura.verified_report(self.channel.pk, job.pk)
            self.assertEqual(error.exception.code, "invalid")

    def test_verified_result_rejects_job_and_chunk_changes_after_child(self):
        cases = (
            ("job", "etag", "changed"),
            ("job", "created_at", timezone.now()),
            ("job", "expires_at", timezone.now() - timezone.timedelta(seconds=1)),
            ("job", "offset", 1),
            ("job", "sha256", "0" * 64),
            ("chunk", "sha256", "0" * 64),
            ("chunk", "size_bytes", 1),
            ("chunk", "summary", {"kind": "file_chunk", "scope": "EU", "offset": 1}),
            ("chunk", "expires_at", timezone.now() - timezone.timedelta(seconds=1)),
            ("chunk", "media_type", "text/plain"),
            ("chunk", "fetched_at", timezone.now()),
        )
        for target, field, value in cases:
            with self.subTest(target=target, field=field):
                job, summary = self.report_copy()

                def child(
                    job_id, *, job=job, target=target, field=field, value=value, summary=summary
                ):
                    row = (
                        DownloadJob.objects.filter(pk=job.pk)
                        if target == "job"
                        else DataSnapshot.objects.filter(pk=job.chunks.first().pk)
                    )
                    row.update(**{field: value})
                    return summary

                with (
                    patch("core.natura_runner.run_report", side_effect=child),
                    self.assertRaises(ReportRefused) as error,
                ):
                    natura.verified_report(self.channel.pk, job.pk)
                self.assertEqual(error.exception.code, "invalid")

    def test_verified_result_invalid_ids_have_no_queries_or_child(self):
        for pk, job in ((True, 1), (1, False), (0, 1), (1, 0), (1, "1"), (1, 2**63)):
            with (
                self.subTest(pk=pk, job=job),
                CaptureQueriesContext(connection) as queries,
                patch("core.natura_runner.run_report") as child,
                self.assertRaises(ValueError),
            ):
                natura.verified_report(pk, job)
            self.assertEqual(len(queries), 0)
            child.assert_not_called()

    def test_verified_result_preserves_raw_dates_case_truncated_examples_and_quality(self):
        rows = [
            site(LATITUDE="x" * 201, DATE_COMPILATION="1900-01-01"),
            site(COUNTRY_CODE="de", SITECODE="DE1", DATE_COMPILATION="2025-01-01"),
        ]
        job, summary = self.report_copy(rows)
        with patch("core.natura_runner.run_report", return_value=summary):
            result = natura.verified_report(self.channel.pk, job.pk)
        self.assertEqual(result["summary"], summary)
        self.assertEqual(summary["country_counts_raw"], {"PL": 1, "de": 1})
        self.assertEqual(summary["date_ranges"]["DATE_COMPILATION"]["max_raw"], "2025-01-01")
        self.assertEqual(summary["quality_status"], "warn")
        self.assertTrue(
            any(example.get("raw_truncated") for example in summary["quality_examples"])
        )

    def test_verified_result_can_pass_quality_without_blanks_or_anomalies(self):
        values = dict.fromkeys(FIELDS, "x")
        values.update({field: "2020-01-01" for field in FIELDS if field.startswith("DATE_")})
        values.update(
            COUNTRY_CODE="PL",
            SITECODE="PL1",
            AREAHA="1",
            LENGTHKM="1",
            MARINE_AREA_PERCENTAGE="0",
            LATITUDE="51",
            LONGITUDE="15",
        )
        job, summary = self.report_copy([site(**values)])
        with patch("core.natura_runner.run_report", return_value=summary):
            result = natura.verified_report(self.channel.pk, job.pk)
        self.assertEqual(result["summary"]["quality_status"], "pass")

    def test_verified_result_nested_date_and_example_schema_is_strict(self):
        job, summary = self.report_copy([site(LATITUDE="x" * 201, DATE_COMPILATION="2025-01-01")])
        mutations = (
            ("date_ranges", {"OTHER": {}}),
            (
                "date_ranges",
                {
                    "DATE_COMPILATION": {
                        "min_raw": "2025-02-30",
                        "max_raw": "2025-02-30",
                        "count": 1,
                        "after_release": 1,
                    }
                },
            ),
            (
                "date_ranges",
                {
                    "DATE_COMPILATION": {
                        "min_raw": "2026-01-01",
                        "max_raw": "2025-01-01",
                        "count": 1,
                        "after_release": 1,
                    }
                },
            ),
            (
                "date_ranges",
                {
                    "DATE_COMPILATION": {
                        "min_raw": "2025-01-01",
                        "max_raw": "2025-01-01",
                        "count": True,
                        "after_release": 1,
                    }
                },
            ),
            ("quality_examples", []),
            ("quality_examples", [{"sitecode": "XX1", "field": "LATITUDE", "raw": "x"}] * 2),
            ("quality_examples", [{"sitecode": "PL1", "field": "LATITUDE", "raw": "x" * 201}] * 2),
            (
                "quality_examples",
                [{"sitecode": "PL1", "field": "LATITUDE", "raw": "x", "raw_truncated": True}] * 2,
            ),
            (
                "quality_examples",
                [{"sitecode": "PL1", "field": "LATITUDE", "raw": "x" * 200, "raw_truncated": 1}]
                * 2,
            ),
        )
        for field, value in mutations:
            with (
                self.subTest(field=field, value=value),
                patch("core.natura_runner.run_report", return_value={**summary, field: value}),
                self.assertRaises(ReportRefused) as error,
            ):
                natura.verified_report(self.channel.pk, job.pk)
            self.assertEqual(error.exception.code, "invalid")

    def test_verified_result_chunk_count_is_bounded_and_expiry_checked_before_child(self):
        for kind in ("missing", "extra", "expired"):
            with self.subTest(kind=kind):
                job, _ = self.report_copy()
                chunk = job.chunks.first()
                if kind == "missing":
                    chunk.delete()
                elif kind == "extra":
                    chunk.pk = None
                    chunk.save()
                else:
                    job.chunks.filter(pk=chunk.pk).update(
                        expires_at=timezone.now() - timezone.timedelta(seconds=1)
                    )
                with (
                    patch("core.natura_runner.run_report") as child,
                    self.assertRaises(ReportRefused) as error,
                ):
                    natura.verified_report(self.channel.pk, job.pk)
                self.assertEqual(error.exception.code, "invalid")
                child.assert_not_called()

    def test_verified_result_propagates_fixed_runner_refusals_without_report(self):
        job, _ = self.report_copy()
        for code in ("busy", "timeout", "invalid", "unavailable"):
            with (
                self.subTest(code=code),
                patch("core.natura_runner.run_report", side_effect=ReportRefused(code)),
                self.assertRaises(ReportRefused) as error,
            ):
                natura.verified_report(self.channel.pk, job.pk)
            self.assertEqual(error.exception.code, code)
        with (
            patch("core.natura._metadata", side_effect=DatabaseError("not exposed")),
            self.assertRaises(ReportRefused) as error,
        ):
            natura.verified_report(self.channel.pk, job.pk)
        self.assertEqual(str(error.exception), "unavailable")

    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="1" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=profile.REGISTRY_ID, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            name="Natura 2000",
            slug=profile.SLUG,
            kind="file",
            region="EU",
            url=profile.URL,
            config=profile.CONFIG,
            access_approved=True,
            attribution=profile.ATTRIBUTION,
        )
        self.job = self.make_copy()
        self.user = get_user_model().objects.create_user(username="natura-test", is_staff=True)
        self.client.force_login(self.user)
        self.factory = RequestFactory()
        self.url = reverse("natura_report", args=[self.channel.pk])

    def make_copy(self, **overrides):
        fields = {
            "channel": self.channel,
            "url": profile.URL,
            "config_hash": configuration(self.channel),
            "status": "complete",
            "offset": profile.SIZE,
            "total_bytes": profile.SIZE,
            "sha256": SHA256,
            "expires_at": timezone.now() + timezone.timedelta(days=1),
        }
        fields.update(overrides)
        return DownloadJob.objects.create(**fields)

    def headers(self, response, status):
        self.assertEqual(response.status_code, status)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        return response

    def direct(self, query="", *, pk=None, user=None, method="get"):
        request = getattr(self.factory, method)(self.url + query)
        request.user = user if user is not None else self.user
        with (
            patch("core.natura_runner.run_report", side_effect=AssertionError("No runner")),
            patch(
                "ingestion.natura_sites.read_verified_sites", side_effect=AssertionError("No CSV")
            ),
            patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")),
            CaptureQueriesContext(connection) as queries,
        ):
            response = natura.report(request, self.channel.pk if pk is None else pk)
        self.assertFalse(any("payload" in q["sql"].lower() for q in queries))
        self.assertFalse(
            any(
                q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )
        return response, queries

    def test_anonymous_nonstaff_inactive_redirect_before_metadata_even_on_post(self):
        users = [
            AnonymousUser(),
            get_user_model()(username="nonstaff", is_staff=False, is_active=True),
            get_user_model()(username="inactive", is_staff=True, is_active=False),
        ]
        for user in users:
            for method in ("get", "post"):
                for query in ("?job=bad", "?job=" + str(self.job.pk)):
                    with (
                        self.subTest(user=user, method=method, query=query),
                        patch("core.natura.verified_report") as compute,
                    ):
                        response, queries = self.direct(query, user=user, method=method)
                        self.headers(response, 302)
                        self.assertTrue(
                            response["Location"].startswith(reverse("login") + "?next=")
                        )
                        self.assertEqual(len(queries), 0)
                        compute.assert_not_called()

    def test_staff_non_get_refused_before_queries(self):
        for method in ("head", "post", "put", "delete", "options"):
            with self.subTest(method=method):
                response, queries = self.direct(method=method)
                self.headers(response, 405)
                self.assertEqual(response["Allow"], "GET")
                self.assertEqual(len(queries), 0)

    def test_strict_query_rejects_invalid_unknown_repeated_and_oversize_without_queries(self):
        for query in (
            "?unknown=1",
            "?job=1&job=1",
            "?job=",
            "?job=0",
            "?job=-1",
            "?job=%2B1",
            "?job=1.0",
            "?job=%201",
            "?job=NaN",
            "?job=１２",
            "?job=9223372036854775808",
            "?job=" + "9" * 1000,
        ):
            with self.subTest(query=query[:60]):
                response, queries = self.direct(query)
                self.headers(response, 400)
                self.assertEqual(len(queries), 0)

    def test_channel_id_range_checked_before_queries(self):
        for pk in (0, -1, 2**63):
            response, queries = self.direct(pk=pk)
            self.headers(response, 400)
            self.assertEqual(len(queries), 0)
        self.headers(self.direct(pk=2**63 - 1)[0], 404)

    def test_missing_and_wrong_profile_channel_fail_closed_without_payload(self):
        self.headers(self.direct(pk=self.channel.pk + 1000)[0], 404)
        changes = {
            "slug": "other",
            "kind": "table",
            "region": "PL",
            "url": "https://example.org/other.csv",
            "config": {},
            "access_approved": False,
            "attribution": "",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                original = getattr(self.channel, field)
                setattr(self.channel, field, value)
                self.channel.save(update_fields=[field])
                self.headers(self.direct()[0], 404)
                setattr(self.channel, field, original)
                self.channel.save(update_fields=[field])

    def test_read_only_repeatable_read_metadata_queries_and_no_chunk_select(self):
        DataSnapshot.objects.create(
            channel=self.channel,
            download_job=self.job,
            payload=b"not a CSV",
            size_bytes=9,
            sha256="0" * 64,
            expires_at=self.job.expires_at,
        )
        before = list(DownloadJob.objects.values()), list(DataSnapshot.objects.values())
        response, queries = self.direct()
        self.headers(response, 200)
        sql = "\n".join(q["sql"] for q in queries)
        self.assertIn("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY", sql)
        self.assertEqual(sum('FROM "ingestion_datachannel"' in q["sql"] for q in queries), 1)
        self.assertEqual(sum('FROM "ingestion_downloadjob"' in q["sql"] for q in queries), 1)
        self.assertNotIn("ingestion_datasnapshot", sql)
        self.assertEqual(
            before, (list(DownloadJob.objects.values()), list(DataSnapshot.objects.values()))
        )
        self.assertContains(response, "Tylko metadane")
        self.assertNotContains(response, "Struktura PASS")

    def test_copy_list_is_bounded_newest_first_and_only_current_complete_profile(self):
        for _ in range(12):
            self.make_copy()
        newest = list(
            DownloadJob.objects.order_by("-created_at", "-pk").values_list("pk", flat=True)[:10]
        )
        for fields in (
            {"status": "queued"},
            {"expires_at": timezone.now()},
            {"url": "https://example.org/other.csv"},
            {"config_hash": "0" * 64},
        ):
            self.make_copy(**fields)
        response = self.headers(self.client.get(self.url), 200)
        self.assertEqual([job["id"] for job in response.context["copies"]], newest)
        self.assertIsNone(response.context["selected"])
        self.assertContains(response, "Oblicz raport kopii nr", count=10)

    def test_transaction_settings_are_effective_during_metadata_read(self):
        original = configuration

        def checked(channel):
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_isolation")
                self.assertEqual(cursor.fetchone()[0], "repeatable read")
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone()[0], "on")
            return original(channel)

        with patch("core.natura.configuration", side_effect=checked) as guard:
            self.headers(self.direct()[0], 200)
        guard.assert_called_once()

    def test_explicit_job_is_pinned_even_when_outside_list(self):
        job, summary = self.report_copy()
        for _ in range(11):
            self.make_copy()
        with patch("core.natura_runner.run_report", return_value=summary):
            response = self.headers(self.client.get(self.url, {"job": job.pk}), 200)
        self.assertEqual(response.context["selected"]["id"], job.pk)
        self.assertEqual(response.context["copies"], [])
        self.assertContains(response, SHA256)
        self.assertContains(response, "end2021 rev1")
        self.assertContains(response, "nie aktualny rejestr")

    def test_missing_expired_incomplete_wrong_url_and_config_jobs_never_fall_back(self):
        jobs = [
            self.make_copy(**fields)
            for fields in (
                {"expires_at": timezone.now()},
                {"status": "running"},
                {"url": "https://example.org/other.csv"},
                {"config_hash": "0" * 64},
            )
        ]
        for job_id in [job.pk for job in jobs] + [2**63 - 1]:
            with self.subTest(job=job_id):
                self.headers(self.direct("?job=" + str(job_id))[0], 404)

    def test_job_of_other_channel_never_falls_back(self):
        self.channel.pk = None
        self.channel.slug = "other-source"
        self.channel.save()
        foreign = self.make_copy()
        original_channel_id = self.job.channel_id
        response, _ = self.direct("?job=" + str(foreign.pk), pk=original_channel_id)
        self.headers(response, 404)

    def test_empty_list_is_valid_but_explicit_missing_copy_is_not(self):
        self.job.status = "queued"
        self.job.save(update_fields=["status"])
        response = self.headers(self.client.get(self.url), 200)
        self.assertEqual(response.context["copies"], [])
        self.assertContains(response, "Brak ważnych kompletnych kopii")
        self.headers(self.direct("?job=" + str(self.job.pk))[0], 404)

    def test_html_escapes_name_and_attribution_and_only_uses_constant_source_links(self):
        self.channel.name = '<script>alert("name")</script>'
        self.channel.attribution = '<a href="javascript:alert(1)">unsafe</a>'
        self.channel.save(update_fields=["name", "attribution"])
        response = self.headers(self.client.get(self.url), 200)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")
        self.assertNotContains(response, '<a href="javascript:')
        self.assertContains(response, profile.URL)
        self.assertContains(response, "https://creativecommons.org/licenses/by/4.0/")

    def test_no_fresh_integrity_claim_from_recorded_metadata(self):
        self.job.sha256 = "0" * 64
        self.job.total_bytes = 1
        self.job.save(update_fields=["sha256", "total_bytes"])
        response = self.headers(self.client.get(self.url), 200)
        self.assertContains(response, "nie zostały sprawdzone")
        self.assertNotContains(response, "Zweryfikowany SHA-256")
        self.assertNotContains(response, "Struktura PASS")

    def test_nested_transaction_and_unsupported_database_refuse_before_metadata(self):
        with transaction.atomic():
            self.headers(self.direct()[0], 503)
        with patch.object(connection, "vendor", "sqlite"):
            response, queries = self.direct()
            self.headers(response, 503)
            self.assertEqual(response["Retry-After"], "5")
            self.assertEqual(len(queries), 0)

    def test_routed_error_responses_keep_security_headers(self):
        self.headers(self.client.get(self.url + "?job=bad"), 400)
        self.headers(self.client.get(reverse("natura_report", args=[2**63 - 1])), 404)
        self.headers(self.client.post(self.url), 405)
        self.client.logout()
        self.headers(self.client.get(self.url), 302)
