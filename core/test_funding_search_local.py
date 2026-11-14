import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from ingestion import funding_calls as calls
from ingestion.funding_search import PORTAL
from ingestion.models import DataChannel, DataSnapshot, RegistryEntry, RegistryRelease
from ingestion.test_funding_calls import HORIZON, NOW, search_client
from ingestion.test_funding_calls import row as search_row


class FundingLocalSearchTests(TestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="test", sha256="d" * 64, sheets={})
        registry = RegistryEntry.objects.create(
            release=release, registry_id=189, master={}, technical={}
        )
        self.channel = DataChannel.objects.create(
            registry=registry,
            slug=calls.slug("horizon"),
            name="Horizon",
            kind="table",
            region="EU",
            url=calls.BASE,
            config=calls.config("horizon"),
            access_approved=True,
            enabled=True,
        )
        self.payload, self.media, self.summary = calls.collect(
            self.channel, search_client(HORIZON), NOW
        )
        self.summary["as_of"] = NOW.isoformat()

    def copy(self, count=None, **extra):
        data = json.loads(self.payload)
        summary = dict(self.summary)
        if count:
            row = next(r for r in data["records"] if r["state"] == "open")
            data["records"] = [
                {
                    **row,
                    "identifier": f"HORIZON-CL5-2026-X-{i:03}",
                    "source_url": calls.PORTAL.rsplit("calls-for-proposals", 1)[0]
                    + f"topic-details/HORIZON-CL5-2026-X-{i:03}",
                }
                for i in range(count)
            ]
            data["state_counts"] = {
                state: count if state == "open" else 0 for state in calls.STATES
            }
            data["source_total"] = count
            data["outside_scope_count"] = 0
            summary.update(
                records=count,
                source_count=count,
                outside_scope=0,
                completeness="converged_passes" if count > 50 else "single_page",
                scan_passes=2 if count > 50 else 1,
                **{"state_" + k: v for k, v in data["state_counts"].items()},
            )
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        now = timezone.now()
        return DataSnapshot.objects.create(
            channel=self.channel,
            payload=raw,
            size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            media_type=self.media,
            summary=summary,
            fetched_at=now,
            expires_at=now + timedelta(days=30),
            **extra,
        )

    def page(self, params=None):
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            return self.client.get(
                reverse("funding_local_search", args=[self.channel.pk]), params or {}
            )

    def change(self, snapshot, **fields):
        data = json.loads(bytes(snapshot.payload))
        data.update(fields)
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        snapshot.payload, snapshot.size_bytes, snapshot.sha256 = (
            raw,
            len(raw),
            hashlib.sha256(raw).hexdigest(),
        )
        snapshot.save()

    def test_filters_and_original_states_are_not_recomputed(self):
        snapshot = self.copy()
        before = DataSnapshot.objects.values().get(pk=snapshot.pk)
        response = self.page({"identifier": "cl5", "title": "CALL ", "state": "open"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(response.context["page"].object_list[0]["state"], "open")
        self.assertContains(response, "w chwili pobrania")
        self.assertEqual(DataSnapshot.objects.values().get(pk=snapshot.pk), before)
        self.assertEqual(
            self.page({"identifier": "CL6", "state": "open"}).context["page"].paginator.count, 0
        )

    def test_50_rows_and_pin_survive_newer_copy(self):
        snapshot = self.copy(60)
        first = self.page()
        self.assertEqual(len(first.context["page"]), 50)
        self.assertIn(f"snapshot={snapshot.pk}", first.context["links"]["next"])
        self.copy(2)
        second = self.page({"snapshot": snapshot.pk, "page": 2})
        self.assertEqual(len(second.context["page"]), 10)
        self.assertEqual(second.context["copy"]["snapshot"].pk, snapshot.pk)
        self.assertEqual(second.context["form"]["snapshot"].value(), str(snapshot.pk))

    def test_empty_copy_missing_copy_and_no_matches_are_distinct(self):
        self.assertContains(self.page(), "Brak aktualnej kopii")
        self.copy()
        self.assertContains(self.page({"title": "not-in-this-copy"}), "Brak dopasowań")

    def test_unknown_repeated_invalid_parameters_and_post_refused(self):
        for query in [
            "unknown=x",
            "state=other",
            "page=0",
            "page=101",
            "snapshot=-1",
            "title=a&title=b",
            "identifier=" + "x" * 201,
        ]:
            with self.subTest(query=query):
                response = self.client.get(
                    reverse("funding_local_search", args=[self.channel.pk]) + "?" + query
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.post(reverse("funding_local_search", args=[self.channel.pk])).status_code,
            405,
        )

    def test_unapproved_wrong_registry_slug_and_non_dict_profile_are_404(self):
        for field, value in [
            ("access_approved", False),
            ("slug", "other"),
            ("config", []),
            ("url", "https://example.org"),
        ]:
            original = getattr(self.channel, field)
            setattr(self.channel, field, value)
            self.channel.save(update_fields=[field])
            with self.subTest(field=field):
                self.assertEqual(self.page().status_code, 404)
            setattr(self.channel, field, original)
            self.channel.save(update_fields=[field])

    def test_expired_and_foreign_pins_do_not_fall_back(self):
        snapshot = self.copy()
        self.assertEqual(self.page({"snapshot": snapshot.pk + 999}).status_code, 404)
        snapshot.fetched_at = timezone.now() - timedelta(days=31)
        snapshot.save(update_fields=["fetched_at"])
        self.assertEqual(self.page({"snapshot": snapshot.pk}).status_code, 404)

    def test_actual_byte_budget_before_payload_select(self):
        snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(payload=b"x" * (5 * 1024 * 1024 + 1))
        with CaptureQueriesContext(connection) as queries:
            response = self.page()
        self.assertEqual(response.status_code, 409)
        blob_selects = [
            q["sql"] for q in queries if 'SELECT "ingestion_datasnapshot"."payload"' in q["sql"]
        ]
        self.assertEqual(blob_selects, [])

    def test_sha_bad_json_and_unfiltered_bad_row_refused(self):
        snapshot = self.copy()
        for data in [
            {"records": [{"identifier": "bad"}]},
            {"profile": "life"},
            {"source_url": "https://example.org"},
            {"state_counts": {"open": True}},
        ]:
            with self.subTest(data=data):
                self.change(snapshot, **data)
                self.assertEqual(self.page({"title": "not-matching"}).status_code, 409)
                snapshot.delete()
                snapshot = self.copy()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(sha256="0" * 64)
        self.assertEqual(self.page().status_code, 409)

    def test_legacy_completeness_not_invented_and_new_heuristic_visible(self):
        snapshot = self.copy(60)
        self.assertContains(self.page(), "Zbieżność przebiegów nie jest dowodem kompletności")
        summary = dict(snapshot.summary)
        for key in ("completeness", "scan_passes", "duplicate_rows_dropped", "retention_basis"):
            summary.pop(key, None)
        snapshot.summary = summary
        snapshot.save(update_fields=["summary"])
        self.assertContains(self.page(), "Brak zapisanego dowodu kompletności")

    @override_settings(DATA_AUTO_MAX_BYTES=1)
    def test_current_limit_reduction_fails_closed(self):
        self.copy()
        self.assertEqual(self.page().status_code, 409)

    def test_null_paging_evidence_is_not_legacy_and_competitive_identity_is_composite(self):
        snapshot = self.copy()
        summary = dict(snapshot.summary)
        summary.pop("scan_passes")
        summary["completeness"] = None
        snapshot.summary = summary
        snapshot.save(update_fields=["summary"])
        self.assertEqual(self.page().status_code, 409)
        snapshot.delete()
        snapshot = self.copy(2)
        data = json.loads(bytes(snapshot.payload))
        for number, row in enumerate(data["records"], 1):
            row.update(
                identifier="HORIZON-CL5-2026-X-001",
                source_type_raw="8",
                source_reference=f"{number}COMPETITIVE_CALLen",
                source_url=calls.PORTAL.rsplit("calls-for-proposals", 1)[0]
                + f"competitive-calls-cs/{number}",
            )
        self.change(snapshot, records=data["records"])
        self.assertEqual(self.page().context["page"].paginator.count, 2)
        data["records"][1].pop("source_reference")
        self.change(snapshot, records=data["records"])
        self.assertEqual(self.page().status_code, 409)

    def life_portal_copy(self):
        self.channel.slug = calls.slug("life")
        self.channel.config = calls.config("life")
        self.channel.save(update_fields=["slug", "config"])
        source = search_row(
            "LIFE-2025-CET-EUCF", ["2027-01-20T17:00:00+01:00"], framework="43252405"
        )
        url = PORTAL + "competitive-calls-cs/48256791"
        source["url"] = url
        source["metadata"].update(url=[url], type=["8"])
        self.payload, self.media, self.summary = calls.collect(
            self.channel, search_client([source], "life"), NOW
        )
        return self.copy()

    def test_life_portal_call_round_trip_from_collector_to_pinned_view(self):
        snapshot = self.life_portal_copy()
        before = DataSnapshot.objects.values().get(pk=snapshot.pk)
        with patch(
            "ingestion.transport.Client.funding_search", side_effect=AssertionError("No HTTP")
        ):
            response = self.page({"snapshot": snapshot.pk, "identifier": "LIFE-2025-CET-EUCF"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["copy"]["snapshot"].pk, snapshot.pk)
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertContains(response, PORTAL + "competitive-calls-cs/48256791")
        self.assertContains(response, "w chwili pobrania")
        self.assertEqual(DataSnapshot.objects.values().get(pk=snapshot.pk), before)

    def test_life_portal_call_reader_rejects_wrong_type_and_url_before_filter(self):
        snapshot = self.life_portal_copy()
        original = json.loads(bytes(snapshot.payload))["records"][0]
        url = original["source_url"]
        variants = [
            {"source_type_raw": "1"},
            {"source_type_raw": "2"},
            {"source_url": url.replace("ec.europa.eu", "example.org")},
            {"source_url": url.replace("ec.europa.eu", "ec.europa.eu.example.org")},
            {"source_url": url.replace("https:", "http:")},
            {"source_url": url + "?extra=1"},
            {"source_url": url + "#extra"},
            {"source_url": url + "/extra"},
            {"source_url": url.replace("48256791", "invalid")},
            {"source_url": url.replace("48256791", "1234567890123")},
            {"source_reference": "16821COMPETITIVE_CALLen"},
        ]
        for changes in variants:
            self.change(snapshot, records=[{**original, **changes}])
            with self.subTest(changes=changes):
                self.assertEqual(self.page({"title": "no-match"}).status_code, 409)

    def test_bad_summary_calendar_and_identity_refused_before_filter(self):
        for damage in ("summary", "date", "duplicate", "url", "state", "reference"):
            snapshot = self.copy()
            if damage == "summary":
                snapshot.summary = []
                snapshot.save(update_fields=["summary"])
            else:
                data = json.loads(bytes(snapshot.payload))
                row = data["records"][-1]
                if damage == "date":
                    row["deadline_dates_raw"] = ["2026-02-30T00:00:00Z"]
                elif damage == "duplicate":
                    data["records"][-1] = data["records"][0]
                elif damage == "url":
                    row["source_url"] = "javascript:alert(1)"
                elif damage == "state":
                    row["state"] = "open"
                elif damage == "reference":
                    row["source_reference"] = "unexpected"
                self.change(snapshot, records=data["records"])
            with self.subTest(damage=damage):
                self.assertEqual(self.page({"title": "no-match"}).status_code, 409)
            snapshot.delete()

    def test_noncanonical_duplicate_key_json_refused_even_with_matching_sha(self):
        snapshot = self.copy()
        raw = bytes(snapshot.payload).replace(b'"schema":', b'"profile":"horizon","schema":', 1)
        DataSnapshot.objects.filter(pk=snapshot.pk).update(
            payload=raw, size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()
        )
        self.assertEqual(self.page().status_code, 409)

    def test_copy_metadata_change_between_selects_refuses(self):
        snapshot = self.copy()
        changed = False

        def wrapper(execute, sql, params, many, context):
            nonlocal changed
            if not changed and sql.startswith('SELECT "ingestion_datasnapshot"."payload"'):
                changed = True
                DataSnapshot.objects.filter(pk=snapshot.pk).update(summary={})
            return execute(sql, params, many, context)

        with connection.execute_wrapper(wrapper):
            response = self.page()
        self.assertTrue(changed)
        self.assertEqual(response.status_code, 409)

    def test_title_escaped_dates_offsets_and_empty_next_deadline_preserved(self):
        snapshot = self.copy()
        data = json.loads(bytes(snapshot.payload))
        data["records"][0]["title"] = '<script>alert("x")</script>'
        self.change(snapshot, records=data["records"])
        response = self.page()
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "2027-01-20T17:00:00+01:00")
        self.assertContains(response, "brak przyszłego terminu w chwili pobrania")

    def test_page_out_of_range_and_foreign_registry_refused(self):
        self.copy()
        self.assertEqual(self.page({"page": 2}).status_code, 400)
        self.channel.registry.registry_id = 188
        self.channel.registry.save(update_fields=["registry_id"])
        self.assertEqual(self.page().status_code, 404)

    def test_empty_verified_copy_and_export_of_whole_filtered_copy(self):
        snapshot = self.copy()
        response = self.page({"state": "open"})
        exported = self.client.get(reverse("data_download", args=[snapshot.pk]))
        self.assertEqual(response.context["page"].paginator.count, 1)
        self.assertEqual(exported.content, bytes(snapshot.payload))
        self.assertEqual(len(json.loads(exported.content)["records"]), 4)
        summary = dict(snapshot.summary)
        summary.update(
            records=0,
            source_count=0,
            outside_scope=0,
            **{"state_" + state: 0 for state in calls.STATES},
        )
        snapshot.summary = summary
        snapshot.save(update_fields=["summary"])
        self.change(
            snapshot,
            records=[],
            source_total=0,
            outside_scope_count=0,
            state_counts=dict.fromkeys(calls.STATES, 0),
        )
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Brak dopasowań")
        self.assertNotContains(response, "Brak aktualnej kopii")
