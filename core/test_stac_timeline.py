from datetime import UTC, datetime, timedelta

from django.test import SimpleTestCase, TransactionTestCase

from core import test_stac_audit as fixtures
from ingestion import stac_audit as audit
from ingestion.stac_timeline import timeline
from ingestion.transport import FetchError


class StacTimelineTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 3, 13, tzinfo=UTC)
        self.scope = {
            "search_start": datetime(2026, 9, 1, 18, tzinfo=UTC),
            "search_end": datetime(2026, 10, 1, 18, tzinfo=UTC),
        }

    def test_partial_utc_days_and_unsearched_tail_not_source_outage(self):
        result = timeline(self.scope, self.now, {})
        self.assertEqual(len(result["days"]), 31)
        self.assertEqual(result["days_outside_frozen_search"], 2)
        self.assertEqual(result["days_partly_searched"], 1)
        self.assertTrue(result["days"][0]["partial_calendar_day"])
        self.assertTrue(result["days"][-1]["partial_calendar_day"])
        self.assertEqual(result["days"][-3]["search_state"], "partly_searched")
        self.assertEqual(result["days"][-1]["search_state"], "outside_frozen_search")
        self.assertTrue(all(not row["source_missing_data_confirmed"] for row in result["days"]))
        self.assertFalse(result["daily_cadence_verified"])

    def test_missing_legacy_dates_not_fabricated_as_zero(self):
        result = timeline(self.scope, self.now, None)
        self.assertFalse(result["observations_available_for_entire_manifest"])
        self.assertIsNone(result["days_without_observations_in_manifest"])
        self.assertTrue(
            all(row["record_occurrences_in_manifest"] is None for row in result["days"])
        )

    def test_exact_midnight_has_no_empty_extra_day(self):
        now = self.now.replace(hour=0)
        result = timeline({"search_start": now - timedelta(days=30), "search_end": now}, now, {})
        self.assertEqual(len(result["days"]), 30)
        self.assertFalse(any(row["partial_calendar_day"] for row in result["days"]))


class StacDatedProofTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacAuditTests.setUp(self)

    def populate(self, pages=1):
        fixtures.StacAuditTests.populate(self, pages)

    def test_dated_proofs_count_unique_ids_across_pages_by_utc_day(self):
        self.populate(26)
        plan = audit.plan(self.channel.pk, self.harvest.pk)["plan"]
        proofs = [audit.batch(plan, start)["proof"] for start in [1, 26]]
        result = audit.combine(plan, proofs)
        days = result["timeline"]["days"]
        self.assertTrue(result["timeline"]["observations_available_for_entire_manifest"])
        self.assertEqual(sum(row["record_occurrences_in_manifest"] for row in days), 52)
        self.assertEqual(sum(row["distinct_scene_ids_in_manifest_day"] for row in days), 2)
        self.assertIsNotNone(result["timeline"]["oldest_verified_observation"])
        self.assertFalse(result["full_current_30_day_coverage_verified"])

    def test_legacy_and_mixed_proofs_preserve_counts_without_complete_timeline(self):
        self.populate(26)
        plan = audit.plan(self.channel.pk, self.harvest.pk)["plan"]
        first = audit.batch(plan, 1)["proof"]
        legacy = audit.unseal(first, "proof", audit.PROOF_BYTES)
        legacy["version"] = 1
        legacy["scenes"] = [row[:2] for row in legacy["scenes"]]
        first = audit.seal(legacy, "proof", audit.PROOF_BYTES)
        tail = audit.batch(plan, 26)["proof"]
        result = audit.combine(plan, [first, tail])
        self.assertEqual(result["record_occurrences_in_manifest"], 52)
        self.assertFalse(result["timeline"]["observations_available_for_entire_manifest"])
        self.assertIsNone(result["timeline"]["oldest_verified_observation"])

    def test_signed_invalid_date_shape_or_scope_rejected(self):
        self.populate()
        plan = audit.plan(self.channel.pk, self.harvest.pk)["plan"]
        proof = audit.unseal(audit.batch(plan, 1)["proof"], "proof", audit.PROOF_BYTES)
        for stamp in ["2026-01-01", "2020-01-01T00:00:00Z", "garbage"]:
            proof["scenes"][0][2] = stamp
            with self.assertRaises(FetchError):
                audit.combine(plan, [audit.seal(proof, "proof", audit.PROOF_BYTES)])
        proof["scenes"][0] = proof["scenes"][0][:2]
        with self.assertRaises(FetchError):
            audit.combine(plan, [audit.seal(proof, "proof", audit.PROOF_BYTES)])
