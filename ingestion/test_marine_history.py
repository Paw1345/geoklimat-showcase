from django.core.exceptions import ValidationError
from django.test import TestCase

from ingestion.marine import CONFIG, PARTIAL_CONFIG, URL
from ingestion.marine_audit import marine_policy_hash
from ingestion.marine_history import MarineAuditRecord, MarineProfileState, record_matches_profile
from ingestion.models import Source


class MarineAuditLedgerTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            slug="registry-195",
            name="Marine",
            adapter="stac",
            url=URL,
            config=CONFIG.copy(),
            enabled=False,
            activation_approved=False,
            last_audit={"status": "verified", "count": 12},
        )
        self.state = MarineProfileState.objects.create(source=self.source)

    def make_record(self, **overrides):
        values = {
            "source": self.source,
            "generation": 0,
            "policy_hash": marine_policy_hash(self.source),
            "audit": {"status": "verified", "count": 12},
        }
        values.update(overrides)
        return MarineAuditRecord.objects.create(**values)

    def test_history_keeps_exact_payload_without_changing_source(self):
        payload = {"status": "manual_review", "nested": {"ids": ["A", "B"]}}
        before = Source.objects.values().get(pk=self.source.pk)
        record = self.make_record(audit=payload)
        record.refresh_from_db()
        self.assertEqual(record.audit, payload)
        self.assertEqual(Source.objects.values().get(pk=self.source.pk), before)

    def test_old_audit_remains_stale_after_profile_round_trip(self):
        record = self.make_record()
        self.assertTrue(record_matches_profile(record, self.state, self.source))
        self.source.config = PARTIAL_CONFIG.copy()
        self.state.generation = 1
        self.state.profile = "partial"
        self.assertFalse(record_matches_profile(record, self.state, self.source))
        self.source.config = CONFIG.copy()
        self.state.generation = 2
        self.state.profile = "strict"
        self.assertFalse(record_matches_profile(record, self.state, self.source))
        current = self.make_record(generation=2)
        self.assertTrue(record_matches_profile(current, self.state, self.source))

    def test_legacy_record_cannot_match_and_malformed_hash_fails_closed(self):
        record = self.make_record(generation=None)
        self.assertFalse(record_matches_profile(record, self.state, self.source))
        record.generation = 0
        record.policy_hash = "ą" * 64
        self.assertFalse(record_matches_profile(record, self.state, self.source))

    def test_persisted_round_trip_keeps_history_and_old_generation_stays_stale(self):
        old = self.make_record()
        self.source.config = PARTIAL_CONFIG.copy()
        self.source.save(update_fields=["config"])
        self.state.profile = "partial"
        self.state.generation = 1
        self.state.save()
        partial = self.make_record(
            generation=1, profile="partial", audit={"status": "manual_review"}
        )
        self.source.config = CONFIG.copy()
        self.source.save(update_fields=["config"])
        self.state.profile = "strict"
        self.state.generation = 2
        self.state.save()
        self.source.refresh_from_db()
        self.state.refresh_from_db()
        self.assertFalse(record_matches_profile(old, self.state, self.source))
        self.assertFalse(record_matches_profile(partial, self.state, self.source))
        self.assertEqual(MarineAuditRecord.objects.count(), 2)
        self.assertTrue(
            record_matches_profile(self.make_record(generation=2), self.state, self.source)
        )

    def test_generation_moves_once_per_change_and_cannot_skip_or_rewind(self):
        self.state.save()
        self.state.profile = "partial"
        self.state.generation = 1
        self.state.save()
        self.state.refresh_from_db()
        self.assertEqual((self.state.profile, self.state.generation), ("partial", 1))
        self.state.generation = 3
        with self.assertRaises(ValidationError):
            self.state.save()
        self.state.profile = "strict"
        self.state.generation = 0
        with self.assertRaises(ValidationError):
            self.state.save()
        self.state.generation = 2
        with self.assertRaises(ValidationError):
            self.state.save(update_fields=["generation"])
        self.state.save()
        self.state.refresh_from_db()
        self.assertEqual((self.state.profile, self.state.generation), ("strict", 2))

    def test_source_and_policy_mismatch_never_match(self):
        record = self.make_record()
        other = Source.objects.create(
            slug="other", name="Other", adapter="stac", url=URL, config=CONFIG
        )
        self.assertFalse(record_matches_profile(record, self.state, other))
        self.source.scope = "changed"
        self.assertFalse(record_matches_profile(record, self.state, self.source))

    def test_missing_or_invalid_lineage_never_matches(self):
        record = self.make_record()
        self.assertFalse(record_matches_profile(None, self.state, self.source))
        self.assertFalse(record_matches_profile(record, None, self.source))
        self.assertFalse(record_matches_profile(record, self.state, None))

    def test_history_validation_rejects_oversize_invalid_hash_and_unknown_shape(self):
        for values in (
            {"audit": {"data": "x" * (2 * 1024 * 1024)}},
            {"policy_hash": "bad"},
            {"audit": []},
            {"generation": -1},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    self.make_record(**values)
        self.assertEqual(MarineAuditRecord.objects.count(), 0)

    def test_ordinary_update_and_delete_of_history_are_rejected(self):
        record = self.make_record()
        record.audit = {"status": "changed"}
        with self.assertRaises(ValidationError):
            record.save()
        with self.assertRaises(ValidationError):
            record.delete()
        self.assertEqual(MarineAuditRecord.objects.get(pk=record.pk).audit["status"], "verified")

    def test_forged_primary_key_cannot_replace_an_existing_history_record(self):
        record = self.make_record()
        forged = MarineAuditRecord(
            pk=record.pk,
            source=self.source,
            generation=0,
            profile="strict",
            policy_hash=marine_policy_hash(self.source),
            audit={"status": "changed"},
        )
        with self.assertRaises(ValidationError):
            forged.save()
        record.refresh_from_db()
        self.assertEqual(record.audit["status"], "verified")

    def test_new_state_cannot_start_at_nonzero_generation(self):
        with self.assertRaises(ValidationError):
            MarineProfileState.objects.create(source=self.source, generation=5)

    def test_state_cannot_be_deleted_or_reset_by_forged_primary_key(self):
        self.state.profile = "partial"
        self.state.generation = 1
        self.state.save()
        with self.assertRaises(ValidationError):
            self.state.delete()
        forged = MarineProfileState(
            pk=self.state.pk, source=self.source, profile="strict", generation=0
        )
        with self.assertRaises(ValidationError):
            forged.save()
        self.state.refresh_from_db()
        self.assertEqual((self.state.profile, self.state.generation), ("partial", 1))

    def test_state_and_history_reject_unrelated_source(self):
        other = Source.objects.create(
            slug="other", name="Other", adapter="stac", url=URL, config=CONFIG
        )
        with self.assertRaises(ValidationError):
            MarineProfileState.objects.create(source=other)
        with self.assertRaises(ValidationError):
            self.make_record(source=other)
