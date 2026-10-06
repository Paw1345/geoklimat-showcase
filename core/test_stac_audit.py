import io
import json
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TransactionTestCase, override_settings

from core import test_stac_integrity as fixtures
from ingestion import stac_audit as audit
from ingestion.models import DataSnapshot
from ingestion.transport import FetchError


class StacAuditTests(TransactionTestCase):
    def setUp(self):
        fixtures.StacIntegrityTests.setUp(self)

    def populate(self, pages=1):
        for page in range(1, pages + 1):
            fixtures.StacIntegrityTests.persist(self, page=page)
        self.harvest.pages = pages
        self.harvest.records = pages * 2
        self.harvest.save(update_fields=["pages", "records"])

    def plan(self):
        return audit.plan(self.channel.pk, self.harvest.pk)

    def test_complete_manifest_cross_batch_uniqueness_and_no_current_coverage_claim(self):
        self.populate(26)
        plan = self.plan()
        self.assertEqual(plan["starts"], [1, 26])
        proofs = [audit.batch(plan["plan"], start)["proof"] for start in plan["starts"]]
        result = audit.combine(plan["plan"], proofs)
        self.assertEqual(result["anchored_pages"], 26)
        self.assertEqual(result["record_occurrences_in_manifest"], 52)
        self.assertEqual(result["distinct_scene_ids_in_manifest"], 2)
        self.assertEqual(result["repeated_occurrences_in_manifest"], 50)
        self.assertTrue(result["all_manifest_pages_verified_at_proof_times"])
        self.assertTrue(result["scan_complete_at_merge"])
        self.assertFalse(result["payloads_rechecked_at_merge"])
        self.assertFalse(result["full_current_30_day_coverage_verified"])
        self.assertFalse(result["geographic_mask_verified"])

    def test_missing_and_duplicate_proof_not_partial_success(self):
        self.populate(26)
        plan = self.plan()["plan"]
        proof = audit.batch(plan, 1)["proof"]
        for proofs in ([], [proof], [proof, proof]):
            with self.assertRaises(FetchError):
                audit.combine(plan, proofs)

    def test_variant_across_batches_counted(self):
        self.populate(25)
        fixtures.StacIntegrityTests.persist(
            self,
            page=26,
            change=lambda data: data["features"][0]["properties"].update(extra="variant"),
        )
        self.harvest.pages = 26
        self.harvest.records = 52
        self.harvest.save(update_fields=["pages", "records"])
        plan = self.plan()["plan"]
        proofs = [audit.batch(plan, start)["proof"] for start in [1, 26]]
        self.assertEqual(
            audit.combine(plan, proofs)["scene_ids_with_multiple_variants_in_manifest"], 1
        )

    def test_tampered_expired_and_foreign_proofs_rejected(self):
        self.populate()
        plan = self.plan()["plan"]
        proof = audit.batch(plan, 1)["proof"]
        with self.assertRaises(FetchError):
            audit.batch(plan + "!", 1)
        with self.assertRaises(FetchError):
            audit.combine(plan, [proof + "!"])
        foreign = audit.unseal(proof, "proof", audit.PROOF_BYTES)
        foreign["plan_sha256"] = "0" * 64
        with self.assertRaises(FetchError):
            audit.combine(plan, [audit.seal(foreign, "proof", audit.PROOF_BYTES)])
        with patch("django.core.signing.time.time", return_value=time.time() + 3602):
            with self.assertRaises(FetchError):
                audit.batch(plan, 1)

    def test_metadata_changed_or_copy_removed_after_plan_refused(self):
        self.populate()
        plan = self.plan()["plan"]
        snapshot = self.channel.snapshots.get()
        snapshot.sha256 = "0" * 64
        snapshot.save(update_fields=["sha256"])
        with self.assertRaisesRegex(FetchError, "inventory changed"):
            audit.batch(plan, 1)
        snapshot.delete()
        with self.assertRaises(FetchError):
            audit.batch(plan, 1)

    def test_plan_refuses_missing_duplicate_expired_and_actual_size_mismatch(self):
        self.populate(2)
        DataSnapshot.objects.filter(summary__page=2).delete()
        with self.assertRaises(FetchError):
            self.plan()
        fixtures.StacIntegrityTests.persist(self, page=2)
        fixtures.StacIntegrityTests.persist(self, page=2)
        with self.assertRaises(FetchError):
            self.plan()
        DataSnapshot.objects.filter(summary__page=2).delete()
        fixtures.StacIntegrityTests.persist(self, page=2)
        DataSnapshot.objects.filter(summary__page=1).update(size_bytes=1)
        with self.assertRaises(FetchError):
            self.plan()

    def test_running_harvest_growth_does_not_expand_last_batch(self):
        self.populate(26)
        self.harvest.status = "running"
        self.harvest.next_url = self.start_url + "&token=next"
        self.harvest.save(update_fields=["status", "next_url"])
        plan = self.plan()["plan"]
        fixtures.StacIntegrityTests.persist(self, page=27)
        self.harvest.pages = 27
        self.harvest.records = 54
        self.harvest.save(update_fields=["pages", "records"])
        first = audit.batch(plan, 1)
        tail = audit.batch(plan, 26)
        self.assertEqual(tail["verification"]["end_page"], 26)
        self.assertEqual(tail["verification"]["checked_pages"], 1)
        self.assertIsNone(tail["verification"]["next_start_page"])
        result = audit.combine(plan, [first["proof"], tail["proof"]])
        self.assertFalse(result["manifest_covers_all_read_pages_at_merge"])
        self.assertFalse(result["scan_complete_at_merge"])

    def test_unaligned_batch_and_evidence_budget_refused(self):
        self.populate(2)
        plan = self.plan()["plan"]
        with self.assertRaises(FetchError):
            audit.batch(plan, 2)
        with patch("ingestion.stac_audit.PLAN_BYTES", 1):
            with self.assertRaises(FetchError):
                self.plan()
        with self.assertRaises(FetchError):
            audit.combine(plan, ["x"] * 401)

    def test_batch_actual_payload_budget_preserved(self):
        self.populate()
        plan = self.plan()["plan"]
        with override_settings(DATA_MANUAL_MAX_BYTES=1):
            with self.assertRaises(FetchError):
                audit.batch(plan, 1)

    def test_plan_and_merge_do_not_read_payloads_or_http(self):
        self.populate()
        with patch("ingestion.transport.Client.get", side_effect=AssertionError("No HTTP")):
            with patch.object(
                DataSnapshot.objects, "only", side_effect=AssertionError("No payload")
            ):
                plan = self.plan()["plan"]
            proof = audit.batch(plan, 1)["proof"]
            with patch.object(
                DataSnapshot.objects, "only", side_effect=AssertionError("No payload")
            ):
                audit.combine(plan, [proof])

    def test_read_only_and_access_guards(self):
        self.populate()
        with patch("ingestion.stac_audit.inventory") as inventory:
            inventory.side_effect = lambda *args: DataSnapshot.objects.update(size_bytes=1)
            with self.assertRaises(DatabaseError):
                self.plan()
        self.channel.access_approved = False
        self.channel.save(update_fields=["access_approved"])
        with self.assertRaises(self.channel.DoesNotExist):
            self.plan()

    def test_configuration_and_observation_retention_changes_refused(self):
        self.populate()
        plan = self.plan()["plan"]
        with patch("ingestion.stac_audit.timezone.now", return_value=self.now + timedelta(days=29)):
            with self.assertRaises(FetchError):
                audit.batch(plan, 1)
        self.channel.config["collection"] = "changed"
        self.channel.save(update_fields=["config"])
        with self.assertRaises(FetchError):
            audit.batch(plan, 1)

    def test_merge_does_not_claim_new_hash_check(self):
        self.populate()
        plan = self.plan()["plan"]
        proof = audit.batch(plan, 1)["proof"]
        snapshot = self.channel.snapshots.get()
        DataSnapshot.objects.filter(pk=snapshot.pk).update(payload=b"!" * snapshot.size_bytes)
        result = audit.combine(plan, [proof])
        self.assertFalse(result["payloads_rechecked_at_merge"])
        self.assertTrue(result["all_manifest_pages_verified_at_proof_times"])
        with self.assertRaisesRegex(FetchError, "SHA256"):
            audit.batch(plan, 1)

    def test_cli_artifacts_merge_no_overwrite_and_invalid_input(self):
        self.populate()
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            proof = Path(directory) / "proof.json"
            report = Path(directory) / "report.json"
            call_command(
                "stac_audit",
                "plan",
                channel=self.channel.pk,
                harvest=self.harvest.pk,
                output=str(plan),
                stdout=io.StringIO(),
            )
            call_command(
                "stac_audit",
                "batch",
                plan=str(plan),
                start=1,
                output=str(proof),
                stdout=io.StringIO(),
            )
            call_command(
                "stac_audit",
                "combine",
                plan=str(plan),
                proof=[str(proof)],
                output=str(report),
                stdout=io.StringIO(),
            )
            self.assertEqual(json.loads(report.read_text())["distinct_scene_ids_in_manifest"], 2)
            original = plan.read_bytes()
            with self.assertRaises(CommandError):
                call_command(
                    "stac_audit",
                    "plan",
                    channel=self.channel.pk,
                    harvest=self.harvest.pk,
                    output=str(plan),
                    stdout=io.StringIO(),
                )
            self.assertEqual(plan.read_bytes(), original)
            with self.assertRaises(CommandError):
                call_command("stac_audit", "batch", plan=str(plan), start=0, stdout=io.StringIO())
            with self.assertRaises(CommandError):
                call_command("stac_audit", "combine", plan=str(plan), stdout=io.StringIO())
