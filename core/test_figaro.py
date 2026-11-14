import hashlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from ingestion import jrc_figaro as figaro
from ingestion.downloads import configuration
from ingestion.models import DataChannel, DataSnapshot, DownloadJob, RegistryEntry, RegistryRelease
from ingestion.test_jrc_figaro import fixture


class FigaroPreviewTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="1" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=181, release=release, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=entry,
            name="FIGARO2015",
            slug=figaro.SLUG,
            kind="file",
            region="EU",
            url=figaro.URL,
            config=figaro.CONFIG,
            access_approved=True,
            attribution="European Commission, Joint Research Centre (JRC), CC BY 4.0.",
        )
        self.body = fixture()
        self.make_copy()
        self.user = get_user_model().objects.create_user(username="figaro-test", is_staff=True)
        self.client.force_login(self.user)
        self.url = reverse("figaro_preview", args=[self.channel.pk])

    def make_copy(self):
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.job = DownloadJob.objects.create(
            channel=self.channel,
            url=self.channel.url,
            config_hash=configuration(self.channel),
            status="complete",
            offset=len(self.body),
            total_bytes=len(self.body),
            sha256=self.digest,
            expires_at=timezone.now() + timezone.timedelta(days=1),
        )
        self.chunk = DataSnapshot.objects.create(
            download_job=self.job,
            channel=self.channel,
            payload=self.body,
            size_bytes=len(self.body),
            sha256=self.digest,
            summary={"kind": "file_chunk", "scope": "EU", "offset": 0},
            expires_at=self.job.expires_at,
        )

    def page(self, params=None):
        with (
            patch.object(figaro, "SIZE", len(self.body)),
            patch.object(figaro, "SHA256", self.digest),
            patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")),
        ):
            return self.client.get(self.url, params or {})

    def test_whole_copy_filters_and_raw_values_without_writes(self):
        before = list(DownloadJob.objects.values()), list(DataSnapshot.objects.values())
        response = self.page({"area": "PL", "indicator": "CO2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy"]["records"], 3)
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(response.context["page"][0]["colPi"], "P3_S13 ")
        self.assertEqual(response.context["page"][0]["obsValue"], "-0")
        self.assertContains(response, "2015")
        self.assertContains(response, "Nie sumujemy")
        self.assertContains(response, reverse("file_download", args=[self.job.pk]))
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(
            before, (list(DownloadJob.objects.values()), list(DataSnapshot.objects.values()))
        )

    def test_anonymous_and_nonstaff_never_read_copy(self):
        with patch(
            "ingestion.jrc_figaro.read_verified_figaro", side_effect=AssertionError("No read")
        ):
            self.client.logout()
            self.assertEqual(self.page().status_code, 302)
            self.user.is_staff = False
            self.user.save(update_fields=["is_staff"])
            self.client.force_login(self.user)
            self.assertEqual(self.page().status_code, 302)

    def test_pagination_pins_job_and_filters_reset_page(self):
        self.body = fixture([f"Air emissions,CO2,PL,S{i:03},2015,kt,{i}" for i in range(60)])
        self.make_copy()
        first = self.page({"area": "PL", "indicator": "CO2"})
        pinned = self.job.pk
        self.assertEqual(len(first.context["page"]), 50)
        self.assertIn(f"job={pinned}", first.context["links"]["next"])
        self.make_copy()
        second = self.page({"job": pinned, "page": 2, "area": "PL", "indicator": "CO2"})
        self.assertEqual(second.context["copy"]["job_id"], pinned)
        self.assertEqual(len(second.context["page"]), 10)
        self.assertEqual(second.context["page"][0]["colPi"], "S050")
        self.assertEqual(second.context["form"].data["job"], str(pinned))
        self.assertNotIn("page", second.context["form"].data)
        self.assertIn("area=PL", second.context["links"]["previous"])
        self.assertIn("indicator=CO2", second.context["links"]["previous"])
        self.assertEqual(self.page().context["copy"]["job_id"], self.job.pk)

    def test_unknown_repeated_invalid_and_unpinned_parameters(self):
        for query in [
            "?unknown=1",
            "?area=PL&area=PL",
            "?indicator=CO2&indicator=CH4",
            "?job=1&job=1",
            "?page=1&page=1",
            "?area=pl",
            "?area=XX",
            "?indicator=NO2",
            "?job=-1",
            "?job=9223372036854775808",
            "?page=0",
            "?page=668",
            "?page=NaN",
            "?page=2",
            "?area=%3Cscript%3E",
        ]:
            with self.subTest(query=query):
                with patch.object(
                    figaro, "read_verified_figaro", side_effect=AssertionError("No read")
                ):
                    response = self.client.get(self.url + query)
                self.assertEqual(response.status_code, 400)
                self.assertIsNone(response.context["copy"])
                self.assertNotContains(response, "<script>", status_code=400)

    def test_empty_filter_result_is_not_missing_copy(self):
        response = self.page({"area": "PL", "indicator": "F-gases"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy"]["records"], 3)
        self.assertEqual(response.context["page"].paginator.count, 0)
        self.assertContains(response, "Brak rekordów pasujących")
        self.assertContains(response, reverse("file_download", args=[self.job.pk]))

    def test_bad_row_outside_filters_refuses_whole_copy(self):
        self.body = fixture(["Air emissions,CO2,PL,A,2015,kt,1", "Other,CH4,DE,B,2015,kt,2"])
        self.make_copy()
        response = self.page({"area": "PL", "indicator": "CO2"})
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.context["copy"])
        self.assertNotContains(response, "<table", status_code=409)
        self.assertContains(response, "Nie pokazujemy częściowych", status_code=409)

    def test_corrupt_latest_copy_never_falls_back_to_older(self):
        old = self.job.pk
        self.make_copy()
        self.chunk.payload = self.body.replace(b"-0", b"-9")
        self.chunk.save(update_fields=["payload"])
        self.assertEqual(self.page().status_code, 409)
        response = self.page({"job": old})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy"]["job_id"], old)

    def test_expired_missing_and_incomplete_pins_never_fall_back(self):
        old = self.job.pk
        self.make_copy()
        for state in ["expired", "queued"]:
            with self.subTest(state=state):
                DownloadJob.objects.filter(pk=old).update(status=state)
                self.assertEqual(self.page({"job": old}).status_code, 404)
        DownloadJob.objects.filter(pk=old).update(status="complete", expires_at=timezone.now())
        self.assertEqual(self.page({"job": old}).status_code, 404)
        self.assertEqual(self.page({"job": 9223372036854775807}).status_code, 404)
        self.assertEqual(self.page().context["copy"]["job_id"], self.job.pk)

    def test_no_completed_local_copy_does_not_schedule_download(self):
        DownloadJob.objects.update(status="queued")
        before = list(DownloadJob.objects.values())
        response = self.page()
        self.assertContains(response, "Brak ukończonej, niewygasłej lokalnej kopii")
        self.assertEqual(before, list(DownloadJob.objects.values()))

    def test_out_of_range_page_has_no_results_or_export(self):
        response = self.page({"job": self.job.pk, "page": 2})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(response.context["copy"])
        self.assertContains(response, "Strona poza zakresem", status_code=400)
        self.assertNotContains(response, "Eksportuj", status_code=400)

    def test_disabled_collection_is_allowed_but_revoked_access_is_not(self):
        self.assertFalse(self.channel.enabled)
        self.assertEqual(self.page().status_code, 200)
        self.channel.access_approved = False
        self.channel.save(update_fields=["access_approved"])
        self.assertEqual(self.page().status_code, 404)

    def test_changed_profile_and_foreign_channel_pin_are_refused(self):
        original = self.channel.config
        self.channel.config = {**original, "dataset": "other"}
        self.channel.save(update_fields=["config"])
        self.assertEqual(self.page().status_code, 404)
        self.channel.config = original
        self.channel.save(update_fields=["config"])
        other = DataChannel.objects.create(
            registry=self.channel.registry, slug="other", kind="file"
        )
        DownloadJob.objects.filter(pk=self.job.pk).update(channel=other)
        self.assertEqual(self.page({"job": self.job.pk}).status_code, 404)

    def test_actual_payload_over_budget_is_refused_before_materialization(self):
        self.chunk.payload = self.body + b"x"
        self.chunk.save(update_fields=["payload"])
        with CaptureQueriesContext(connection) as queries:
            response = self.page()
        self.assertEqual(response.status_code, 409)
        for item in queries:
            sql = item["sql"].lower()
            if "select" in sql and '"payload"' in sql:
                self.assertIn("octet_length", sql)

    def test_snapshot_is_read_only_and_channel_is_loaded_inside_it(self):
        original = figaro.read_verified_figaro

        def inspect(job, now):
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_setting('transaction_isolation'), "
                    "current_setting('transaction_read_only')"
                )
                self.assertEqual(cursor.fetchone(), ("repeatable read", "on"))
            return original(job, now)

        with (
            patch.object(figaro, "read_verified_figaro", side_effect=inspect),
            CaptureQueriesContext(connection) as queries,
        ):
            self.assertEqual(self.page().status_code, 200)
        sql = [item["sql"].lower() for item in queries]
        fence = next(
            i for i, statement in enumerate(sql) if statement.startswith("set transaction")
        )
        channel = next(
            i for i, statement in enumerate(sql) if 'from "ingestion_datachannel"' in statement
        )
        self.assertLess(fence, channel)

    def test_enclosing_transaction_fails_closed_without_500(self):
        with transaction.atomic():
            self.assertEqual(self.page().status_code, 409)

    def test_only_get_is_accepted_for_staff(self):
        for method in ["post", "put", "delete", "head"]:
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)

    def test_source_codes_and_attribution_are_autoescaped(self):
        self.body = fixture(["Air emissions,CO2,PL,<script>,2015,kt,-0"])
        self.make_copy()
        self.channel.attribution = "JRC <script>alert(1)</script>"
        self.channel.save(update_fields=["attribution"])
        DownloadJob.objects.filter(pk=self.job.pk).update(config_hash=configuration(self.channel))
        response = self.page()
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")

    def test_original_export_is_unfiltered_and_unchanged(self):
        before = self.client.get(reverse("file_download", args=[self.job.pk]))
        original = b"".join(before.streaming_content)
        self.page({"area": "PL", "indicator": "CO2"})
        after = self.client.get(reverse("file_download", args=[self.job.pk]))
        self.assertEqual(b"".join(after.streaming_content), original)
        self.assertEqual(original, self.body)
        self.assertEqual(after["Content-Disposition"], before["Content-Disposition"])
        self.assertEqual(after["Content-Type"], before["Content-Type"])

    def test_source_area_options_are_deterministic_and_not_current_eu_mask(self):
        response = self.page()
        choices = response.context["form"].fields["area"].choices
        self.assertEqual([code for code, label in choices if code], sorted(figaro.AREAS))
        self.assertIn(("FIGW1", "FIGW1"), choices)
        self.assertIn(("GB", "GB"), choices)
        self.assertIn(("US", "US"), choices)

    def test_unavailable_database_snapshot_fails_closed(self):
        with patch.object(connection, "vendor", "sqlite"):
            self.assertEqual(self.page().status_code, 409)

    def test_large_or_unknown_channel_id_is_not_a_database_overflow(self):
        for pk in [9223372036854775808, 999999]:
            with self.subTest(pk=pk):
                self.assertEqual(
                    self.client.get(reverse("figaro_preview", args=[pk])).status_code, 404
                )
