import json
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from ingestion import test_mwp_preview as fixtures
from ingestion.models import DataChannel, DataSnapshot
from ingestion.test_pig_mwp_tables import chem_row, level_row


class MwpSearchTests(TestCase):
    setUp = fixtures.MwpPreviewTests.setUp
    copy = fixtures.MwpPreviewTests.copy
    change_data = fixtures.MwpPreviewTests.change_data

    def page(self, channel, params=None):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            return self.client.get(reverse("mwp_search", args=[channel.pk]), params or {})

    def test_first_page_is_bounded_pinned_and_keeps_original_copy(self):
        channel, snapshot = self.copy(count=60)
        before = DataSnapshot.objects.values().get(pk=snapshot.pk)
        response = self.page(channel)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 60)
        self.assertEqual(len(response.context["page"].object_list), 50)
        self.assertEqual(response.context["copy"]["snapshot"].pk, snapshot.pk)
        self.assertContains(response, "WSKAZNIK")
        self.assertIn(f"snapshot={snapshot.pk}", response.context["links"]["next"])
        self.assertEqual(DataSnapshot.objects.values().get(pk=snapshot.pk), before)

    def test_dates_are_inclusive_and_station_is_exact_not_a_fragment(self):
        channel, _ = self.copy(count=60)
        response = self.page(
            channel, {"station": 801, "date_from": "2025-10-02", "date_to": "2025-10-02"}
        )
        self.assertEqual(response.context["page"].object_list[0][0], "1")
        self.assertEqual(response.context["page"].paginator.count, 1)
        response = self.page(channel, {"station": 80})
        self.assertEqual(response.context["page"].paginator.count, 0)
        self.assertContains(response, "Brak dopasowań")

    def test_pagination_keeps_old_copy_after_new_snapshot(self):
        channel, snapshot = self.copy(count=60)
        first = self.page(channel)
        newer = DataSnapshot.objects.create(
            channel=channel,
            payload=snapshot.payload,
            size_bytes=snapshot.size_bytes,
            sha256=snapshot.sha256,
            summary=snapshot.summary,
            media_type=snapshot.media_type,
            expires_at=snapshot.expires_at,
        )
        second = self.client.get(
            reverse("mwp_search", args=[channel.pk]) + first.context["links"]["next"]
        )
        self.assertEqual(second.context["copy"]["snapshot"].pk, snapshot.pk)
        self.assertEqual(len(second.context["page"].object_list), 10)
        self.assertEqual(second.context["page"].object_list[0][0], "51")
        self.assertIn(f"snapshot={snapshot.pk}", second.context["links"]["previous"])
        self.assertEqual(self.page(channel).context["copy"]["snapshot"].pk, newer.pk)
        self.assertEqual(second.context["form"]["snapshot"].value(), str(snapshot.pk))
        self.assertNotIn('name="page"', second.content.decode())

    def test_bad_queries_refuse_before_reading_copy(self):
        channel, _ = self.copy()
        cases = [
            {"unknown": "x"},
            {"station": ["1", "2"]},
            {"page": ["1", "1"]},
            {"snapshot": "True"},
            {"snapshot": 2**63},
            {"page": 0},
            {"page": 4001},
            {"station": "x"},
            {"station": 2**53 + 1},
            {"indicator": "x" * 101},
            {"date_from": "2025-02-30"},
            {"date_from": "2025-2-01"},
            {"date_to": "01/10/2025"},
            {"date_from": "2025-10-02", "date_to": "2025-10-01"},
        ]
        for params in cases:
            with self.subTest(params=params), patch("core.mwp_search.read_copy") as read:
                self.assertEqual(self.page(channel, params).status_code, 400)
                read.assert_not_called()

    def test_measurements_reject_chemistry_filter_and_keep_zero_null_units(self):
        channel, _ = self.copy(
            "pomiary",
            make=lambda i: {**level_row(i), "GLEBOKOSC_ZWIERCIADLA": 0 if i == 1 else -4.51},
        )
        response = self.page(channel)
        self.assertEqual(response.context["page"].object_list[0][-2:], ["0", "null"])
        self.assertEqual(
            response.context["copy"]["data"]["units"],
            {"GLEBOKOSC_ZWIERCIADLA": "m p.p.t.", "WYDAJNOSC": "l/s"},
        )
        self.assertContains(response, "-4.51")
        self.assertEqual(self.page(channel, {"indicator": "Brom"}).status_code, 400)

    def test_chemistry_preserves_whitespace_loq_and_escapes_html(self):
        channel, snapshot = self.copy(
            make=lambda i: {
                **chem_row(i),
                "JEDNOSTKA": " ",
                "WSKAZNIK": " <script>alert(1)</script> ",
            }
        )
        response = self.page(channel)
        cells = response.context["page"].object_list
        self.assertEqual(json.loads(cells[0][-1]), " ")
        self.assertEqual(json.loads(cells[2][-3]), chem_row(3)["ZNAK"])
        self.assertEqual(cells[2][-2], "null")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
        exported = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertEqual(exported.content, bytes(snapshot.payload))

    def test_missing_or_other_channel_pin_never_falls_back(self):
        channel, _ = self.copy()
        other, other_snapshot = self.copy("pomiary")
        for pin in (other_snapshot.pk, 2**63 - 1):
            self.assertEqual(self.page(channel, {"snapshot": pin}).status_code, 404)
        self.assertEqual(self.page(other).status_code, 200)

    def test_profile_or_approval_change_is_404_even_with_valid_pin(self):
        channel, snapshot = self.copy()
        cases = [
            {"access_approved": False},
            {"config": []},
            {"config": {}},
            {"region": "EU"},
            {"url": "https://example.invalid/"},
            {"slug": "other"},
            {"attribution": "changed"},
        ]
        for updates in cases:
            before = DataChannel.objects.values().get(pk=channel.pk)
            DataChannel.objects.filter(pk=channel.pk).update(**updates)
            self.assertEqual(self.page(channel, {"snapshot": snapshot.pk}).status_code, 404)
            DataChannel.objects.filter(pk=channel.pk).update(
                **{key: before[key] for key in updates}
            )

    def test_hard_expired_pin_is_404_and_absent_copy_is_explicit(self):
        channel, snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(fetched_at=self.now - timedelta(days=31))
        self.assertEqual(self.page(channel, {"snapshot": snapshot.pk}).status_code, 404)
        response = self.page(channel)
        self.assertContains(response, "Brak aktualnej kopii")
        self.assertNotContains(response, "Brak dopasowań")

    def test_out_of_range_result_page_is_400(self):
        channel, _ = self.copy()
        self.assertEqual(self.page(channel, {"page": 2}).status_code, 400)
        self.assertEqual(self.page(channel, {"station": 0, "page": 2}).status_code, 400)

    def test_entire_copy_checked_before_filter_including_impossible_date(self):
        channel, snapshot = self.copy()

        def corrupt(data):
            data["rows"][1][3] = "2025-02-30"
            data["period"][0] = "2025-02-30"

        self.change_data(snapshot, corrupt)
        summary = {**snapshot.summary, "period_start": "2025-02-30"}
        DataSnapshot.objects.filter(pk=snapshot.pk).update(summary=summary)
        self.assertEqual(self.page(channel, {"station": 801}).status_code, 409)

    def test_invalid_sha_and_actual_bytes_refuse_without_raw_data(self):
        channel, snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(sha256="0" * 64)
        self.assertEqual(self.page(channel).status_code, 409)
        DataSnapshot.objects.filter(pk=snapshot.pk).update(sha256=snapshot.sha256)
        with (
            override_settings(DATA_AUTO_MAX_BYTES=snapshot.size_bytes - 1),
            CaptureQueriesContext(connection) as queries,
        ):
            self.assertEqual(self.page(channel).status_code, 409)
        self.assertFalse(
            any('SELECT "ingestion_datasnapshot"."payload"' in q["sql"] for q in queries)
        )

    def test_page_text_budget_refuses_without_truncating_loq(self):
        channel, _ = self.copy(
            count=60, make=lambda i: {**chem_row(i), "ZNAK": "< LOQ " + "x" * 4000}
        )
        response = self.page(channel)
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(response.context["copy"])
        self.assertNotContains(response, "x" * 100, status_code=409)

    def test_post_is_405_and_get_performs_no_writes(self):
        channel, snapshot = self.copy()
        before = DataSnapshot.objects.values().get(pk=snapshot.pk)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.page(channel).status_code, 200)
        self.assertFalse(
            any(
                q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for q in queries
            )
        )
        self.assertEqual(DataSnapshot.objects.values().get(pk=snapshot.pk), before)
        self.assertEqual(
            self.client.post(reverse("mwp_search", args=[channel.pk])).status_code, 405
        )

    def test_detail_exposes_search_only_when_approved(self):
        channel, _ = self.copy()
        target = reverse("mwp_search", args=[channel.pk])
        self.assertContains(self.client.get(reverse("data_detail", args=[channel.pk])), target)
        DataChannel.objects.filter(pk=channel.pk).update(access_approved=False)
        self.assertNotContains(self.client.get(reverse("data_detail", args=[channel.pk])), target)

    def test_all_filters_use_decoded_fields_and_source_dates(self):
        channel, snapshot = self.copy(count=60)
        response = self.page(
            channel,
            {
                "station": 801,
                "indicator": "bRoM",
                "date_from": "2025-10-01",
                "date_to": "2025-10-05",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 2)
        self.assertEqual(response.context["copy"]["snapshot"].pk, snapshot.pk)

    def test_indicator_folds_each_dictionary_value_once_not_once_per_row(self):
        from datetime import date

        from core.mwp_search import matching_indexes

        _, snapshot = self.copy(count=60)
        data = json.loads(bytes(snapshot.payload))
        calls = []

        class CountedString(str):
            def casefold(self):
                calls.append(self)
                return super().casefold()

        data["dictionaries"]["WSKAZNIK"] = [
            CountedString(s) for s in data["dictionaries"]["WSKAZNIK"]
        ]
        values = {
            "station": None,
            "date_from": date(2025, 10, 1),
            "date_to": None,
            "indicator": "brom",
        }
        matches = list(matching_indexes(data, values))
        self.assertEqual(len(matches), 20)
        self.assertEqual(len(calls), len(data["dictionaries"]["WSKAZNIK"]))
