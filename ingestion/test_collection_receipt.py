"""B1/B2 receipt: test PostgreSQL and temporary files only, no providers/operator data."""

import hashlib
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ingestion.collection_continuity import PILOT_END, PILOT_START
from ingestion.downloads import configuration
from ingestion.models import CollectionAttempt, DataChannel, RegistryEntry, RegistryRelease

NOW = PILOT_START + timedelta(minutes=30)


class DisabledReceiptTests(SimpleTestCase):
    def test_default_off_cli_refuses_without_json_or_queries(self):
        output = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("collection_receipt", channel=[1, 2, 3], stdout=output)
        self.assertEqual(output.getvalue(), "")

    def test_cli_has_output_but_no_clock_or_origin_override(self):
        from ingestion.management.commands.collection_receipt import Command

        parser = Command().create_parser("manage.py", "collection_receipt")
        destinations = {action.dest for action in parser._actions}
        self.assertIn("output", destinations)
        self.assertTrue(destinations.isdisjoint({"now", "as_of", "origin", "status", "batch"}))

    def test_only_literal_true_allows_connection_or_clock(self):
        from ingestion.collection_receipt import ReceiptError, preview

        for flag in (False, None, 0, 1, "true"):
            with self.subTest(flag=flag), override_settings(COLLECTION_EVIDENCE_ENABLED=flag):
                with (
                    patch("ingestion.collection_receipt.connection") as db,
                    patch("ingestion.collection_receipt.timezone.now") as clock,
                    self.assertRaises(ReceiptError),
                ):
                    preview([1, 2, 3])
                self.assertEqual(db.mock_calls, [])
                clock.assert_not_called()

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_invalid_id_choices_fail_before_connection(self):
        from ingestion.collection_receipt import ReceiptError, preview

        for ids in (
            None,
            [],
            [1],
            [1, 2, 2],
            [1, 2, 3, 4],
            [True, 2, 3],
            [0, 2, 3],
            [-1, 2, 3],
            ["1", 2, 3],
            [2**63, 2, 3],
            iter([1, 2, 3]),
        ):
            with self.subTest(ids=ids), patch("ingestion.collection_receipt.connection") as db:
                with self.assertRaises(ReceiptError):
                    preview(ids)
                self.assertEqual(db.mock_calls, [])

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_missing_cli_scope_is_refused_without_queries(self):
        with self.assertRaises(CommandError):
            call_command("collection_receipt", stdout=io.StringIO())


class ReceiptFileTests(SimpleTestCase):
    def setUp(self):
        from ingestion.collection_receipt import encode_receipt

        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "backups" / "collection-receipts"
        self.root.mkdir(parents=True)
        self.settings_override = override_settings(BASE_DIR=self.base)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.output = encode_receipt({"label": "Środowisko", "status": "pending"})

    def export(self, target, output=None):
        stdout = io.StringIO()
        with patch(
            "ingestion.management.commands.collection_receipt.preview",
            return_value=self.output if output is None else output,
        ):
            call_command("collection_receipt", channel=[1, 2, 3], output=target, stdout=stdout)
        return stdout.getvalue()

    def test_new_file_round_trip_exact_utf8_hash_and_stdout(self):
        target = self.root / "próba.json"
        self.assertEqual(self.export(str(target)), self.output)
        self.assertEqual(target.read_bytes(), self.output.encode("utf-8"))
        envelope = json.loads(target.read_bytes())
        canonical = json.dumps(
            envelope["receipt"],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(envelope["receipt_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_existing_file_is_never_truncated(self):
        target = self.root / "existing.json"
        target.write_bytes(b"operator-original")
        with self.assertRaises(CommandError):
            self.export(str(target))
        self.assertEqual(target.read_bytes(), b"operator-original")

    def test_path_escape_relative_traversal_and_non_json_are_refused(self):
        outside = self.base / "outside.json"
        candidates = [
            str(outside),
            "receipt.json",
            "",
            str(self.root / "note.txt"),
            str(self.root / "nested" / "file.json"),
            str(self.root) + "/../outside.json",
            str(self.root) + "/./file.json",
            str(self.root / "file.json") + "\x00",
            "x" * 4097,
        ]
        for target in candidates:
            with self.subTest(target=target), self.assertRaises(CommandError):
                self.export(target)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(outside.exists())

    def test_missing_directory_is_not_created(self):
        self.root.rmdir()
        with self.assertRaises(CommandError):
            self.export(str(self.root / "file.json"))
        self.assertFalse(self.root.exists())

    def test_target_symlink_never_modifies_operator_file(self):
        original = self.base / "original.json"
        original.write_bytes(b"operator-original")
        target = self.root / "link.json"
        target.symlink_to(original)
        with self.assertRaises(CommandError):
            self.export(str(target))
        self.assertTrue(target.is_symlink())
        self.assertEqual(original.read_bytes(), b"operator-original")

    def test_symlinked_output_directory_is_refused(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.root.rmdir()
        self.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(CommandError):
            self.export(str(self.root / "file.json"))
        self.assertEqual(list(outside.iterdir()), [])

    def test_failed_evidence_creates_no_file_or_success_stdout(self):
        from ingestion.collection_receipt import ReceiptError

        stdout = io.StringIO()
        target = self.root / "file.json"
        with (
            patch(
                "ingestion.management.commands.collection_receipt.preview",
                side_effect=ReceiptError("raw secret"),
            ),
            self.assertRaises(CommandError),
        ):
            call_command("collection_receipt", channel=[1, 2, 3], output=str(target), stdout=stdout)
        self.assertFalse(target.exists())
        self.assertEqual(stdout.getvalue(), "")

    def test_symlinked_ancestor_is_refused(self):
        original = self.base / "backups-original"
        (self.base / "backups").rename(original)
        (self.base / "backups").symlink_to(original, target_is_directory=True)
        with self.assertRaises(CommandError):
            self.export(str(self.root / "file.json"))
        self.assertEqual(list((original / "collection-receipts").iterdir()), [])

    def test_existing_json_directory_is_preserved(self):
        target = self.root / "directory.json"
        target.mkdir()
        with self.assertRaises(CommandError):
            self.export(str(target))
        self.assertTrue(target.is_dir())

    def test_two_competing_writers_have_exactly_one_success(self):
        barrier = Barrier(2)
        target = str(self.root / "race.json")

        def receipt(*args):
            barrier.wait(timeout=10)
            return self.output

        def export():
            try:
                call_command(
                    "collection_receipt", channel=[1, 2, 3], output=target, stdout=io.StringIO()
                )
                return "success"
            except CommandError:
                return "refused"

        with patch("ingestion.management.commands.collection_receipt.preview", side_effect=receipt):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: export(), range(2)))
        self.assertCountEqual(results, ["success", "refused"])
        self.assertEqual(Path(target).read_bytes(), self.output.encode("utf-8"))

    def test_directory_swap_cannot_redirect_creation_to_symlink_destination(self):
        outside = self.base / "outside"
        outside.mkdir()
        held_root = self.base / "held-root"
        real_open = os.open

        def swapping_open(path, flags, *args, **kwargs):
            if path == "file.json":
                self.root.rename(held_root)
                self.root.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)

        with patch(
            "ingestion.management.commands.collection_receipt.os.open", side_effect=swapping_open
        ) as safe_open:
            with patch(
                "ingestion.management.commands.collection_receipt.os.supports_dir_fd", {safe_open}
            ):
                self.export(str(self.root / "file.json"))
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual((held_root / "file.json").read_bytes(), self.output.encode("utf-8"))

    def test_unavailable_safe_directory_operations_fail_closed(self):
        with patch("ingestion.management.commands.collection_receipt.os.supports_dir_fd", set()):
            with self.assertRaises(CommandError):
                self.export(str(self.root / "file.json"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_encoding_failure_happens_before_file_creation(self):
        with self.assertRaises(CommandError):
            self.export(str(self.root / "file.json"), output="\ud800")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_partial_write_and_close_errors_are_not_success_or_deleted(self):
        real_fdopen = os.fdopen
        for fault in ("short", "write", "close"):
            target = self.root / (fault + ".json")
            stdout = io.StringIO()

            class FaultStream:
                def __init__(self, *args, fault=fault, **kwargs):
                    self.fault = fault
                    self.stream = real_fdopen(*args, **kwargs)

                def __enter__(self):
                    self.stream.__enter__()
                    return self

                def write(self, data):
                    if self.fault == "close":
                        return self.stream.write(data)
                    count = self.stream.write(data[:10])
                    if self.fault == "write":
                        raise OSError("sensitive disk error")
                    return count

                def __exit__(self, *args):
                    self.stream.__exit__(*args)
                    if self.fault == "close":
                        raise OSError("sensitive close error")

            with (
                self.subTest(fault=fault),
                patch(
                    "ingestion.management.commands.collection_receipt.preview",
                    return_value=self.output,
                ),
                patch("ingestion.management.commands.collection_receipt.os.fdopen", FaultStream),
            ):
                with self.assertRaises(CommandError) as error:
                    call_command(
                        "collection_receipt", channel=[1, 2, 3], output=str(target), stdout=stdout
                    )
                from ingestion.collection_receipt import FAILURE

                self.assertEqual(str(error.exception), FAILURE)
                self.assertTrue(error.exception.__suppress_context__)
                self.assertEqual(stdout.getvalue(), "")
                self.assertTrue(target.exists())
                self.assertEqual(
                    target.read_bytes(),
                    self.output.encode("utf-8")
                    if fault == "close"
                    else self.output.encode("utf-8")[:10],
                )

    def test_real_off_output_creates_no_file_or_queries(self):
        target = self.root / "off.json"
        with override_settings(COLLECTION_EVIDENCE_ENABLED=False), self.assertRaises(CommandError):
            call_command(
                "collection_receipt", channel=[1, 2, 3], output=str(target), stdout=io.StringIO()
            )
        self.assertEqual(list(self.root.iterdir()), [])


class ReceiptEncodingTests(SimpleTestCase):
    def test_unicode_dates_hash_and_final_lf_round_trip(self):
        from ingestion.collection_receipt import encode_receipt

        output = encode_receipt({"label": "Środowisko", "as_of": PILOT_START})
        self.assertIn("Środowisko", output)
        self.assertTrue(output.endswith("\n"))
        envelope = json.loads(output)
        self.assertEqual(envelope["receipt"]["as_of"], "2026-10-15T08:00:00Z")
        canonical = json.dumps(
            envelope["receipt"],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.assertEqual(envelope["receipt_sha256"], hashlib.sha256(canonical).hexdigest())

    def test_invalid_json_values_refuse_with_safe_error(self):
        from ingestion.collection_receipt import FAILURE, ReceiptError, encode_receipt

        for value in (float("nan"), float("inf"), float("-inf"), object(), "\ud800"):
            with self.subTest(value=type(value).__name__), self.assertRaises(ReceiptError) as error:
                encode_receipt({"value": value})
            self.assertEqual(str(error.exception), FAILURE)

    def test_budget_includes_envelope_lf_and_counts_utf8_bytes(self):
        from ingestion.collection_receipt import ReceiptError, encode_receipt

        overhead = len(encode_receipt({"text": ""}).encode("utf-8"))
        room = 65536 - overhead
        self.assertEqual(len(encode_receipt({"text": "x" * room}).encode("utf-8")), 65536)
        for text in ("x" * (room + 1), "ą" * room):
            with self.assertRaises(ReceiptError):
                encode_receipt({"text": text})


@override_settings(COLLECTION_EVIDENCE_ENABLED=True)
class ReceiptPreviewTests(TransactionTestCase):
    def setUp(self):
        release = RegistryRelease.objects.create(filename="fixture", sha256="a" * 64, sheets={})
        entry = RegistryEntry.objects.create(
            registry_id=32, release=release, master={}, technical={}
        )
        self.channels = [
            DataChannel.objects.create(
                registry=entry,
                slug=f"imgw-{profile}",
                name="private channel name",
                kind="imgw",
                region="PL",
                url=f"https://danepubliczne.imgw.pl/api/data/{profile}",
                config={"private_note": "never-export-this"},
                access_approved=True,
                enabled=True,
                interval_minutes=60,
            )
            for profile in ("synop", "meteo", "hydro")
        ]
        self.ids = [channel.pk for channel in self.channels]

    def attempt(self, channel, **changes):
        values = {
            "channel": channel,
            "attempt_id": uuid4(),
            "batch_id": uuid4(),
            "origin": "worker",
            "started_at": PILOT_START,
            "finished_at": PILOT_START + timedelta(seconds=1),
            "due_at": PILOT_START,
            "interval_minutes": 60,
            "profile_sha256": configuration(channel),
            "outcome": "unchanged",
            "reason_code": "",
        }
        return CollectionAttempt.objects.create(**(values | changes))

    def document(self, ids=None):
        from ingestion.collection_receipt import preview

        with patch("ingestion.collection_receipt.timezone.now", return_value=NOW):
            output = preview(self.ids if ids is None else ids)
        self.assertTrue(output.endswith("\n"))
        self.assertLessEqual(len(output.encode("utf-8")), 65536)
        return json.loads(output)

    def test_three_empty_profiles_are_unconfirmed_not_zero_filled_pass(self):
        envelope = self.document()
        receipt = envelope["receipt"]
        self.assertEqual(receipt["assessment_status"], "unconfirmed")
        self.assertEqual(receipt["evidence_status"], "available")
        self.assertEqual(receipt["metadata_retention_days"], 35)
        self.assertEqual(len(receipt["channels"]), 3)
        self.assertTrue(
            all("no_worker_attempts" in row["assessment"]["reasons"] for row in receipt["channels"])
        )

    def test_pending_receipt_has_canonical_hash_and_no_private_fields(self):
        for channel in self.channels:
            self.attempt(channel)
        envelope = self.document(list(reversed(self.ids)))
        receipt = envelope["receipt"]
        self.assertEqual(receipt["assessment_status"], "pending")
        self.assertEqual(receipt["as_of"], "2026-10-15T08:30:00Z")
        self.assertEqual([row["channel_id"] for row in receipt["channels"]], self.ids)
        encoded = json.dumps(
            receipt, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        self.assertEqual(envelope["receipt_sha256"], hashlib.sha256(encoded).hexdigest())
        output = json.dumps(envelope)
        for private in (
            "never-export-this",
            "private_note",
            "private channel name",
            "danepubliczne",
            "batch_id",
            "attempt_id",
            "payload",
        ):
            self.assertNotIn(private, output)
        self.assertEqual(receipt["live_reassessment_deadline"], "2026-11-19T08:00:00Z")

    def test_scope_missing_or_duplicate_profile_refuses_all(self):
        from ingestion.collection_receipt import ReceiptError

        with self.assertRaises(ReceiptError):
            self.document([*self.ids[:2], max(self.ids) + 100])
        DataChannel.objects.filter(pk=self.ids[-1]).update(url=self.channels[0].url)
        with self.assertRaises(ReceiptError):
            self.document()

    def test_real_cap_gives_diagnostic_null_for_every_channel(self):
        for channel in self.channels:
            self.attempt(channel)
        with patch("ingestion.collection_evidence.READ_ATTEMPT_LIMIT", 1):
            receipt = self.document()["receipt"]
        self.assertEqual(receipt["evidence_status"], "capped")
        self.assertEqual(receipt["assessment_status"], "unconfirmed")
        self.assertEqual([row["assessment"] for row in receipt["channels"]], [None] * 3)

    def test_cli_preview_is_json_only_without_files(self):
        from ingestion.collection_receipt import preview

        output = io.StringIO()
        with patch("ingestion.collection_receipt.timezone.now", return_value=NOW):
            with patch("builtins.open", side_effect=AssertionError("No file writes")):
                call_command("collection_receipt", channel=self.ids, stdout=output)
            expected = preview(self.ids)
        self.assertEqual(output.getvalue(), expected)
        self.assertEqual(set(json.loads(output.getvalue())), {"receipt", "receipt_sha256"})

    def test_cli_real_evidence_file_matches_preview_and_preserves_hash(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "backups" / "collection-receipts"
            root.mkdir(parents=True)
            target = root / "test-pg.json"
            stdout = io.StringIO()
            with (
                override_settings(BASE_DIR=base),
                patch("ingestion.collection_receipt.timezone.now", return_value=NOW),
            ):
                call_command(
                    "collection_receipt", channel=self.ids, output=str(target), stdout=stdout
                )
                expected = self.document()
            self.assertEqual(json.loads(target.read_bytes()), expected)
            self.assertEqual(target.read_bytes(), stdout.getvalue().encode("utf-8"))
            self.assertEqual(expected["receipt"]["assessment_status"], "unconfirmed")

    def test_cli_repeated_arguments_match_keyword_list(self):
        output = io.StringIO()
        args = [value for pk in self.ids for value in ("--channel", str(pk))]
        with patch("ingestion.collection_receipt.timezone.now", return_value=NOW):
            call_command("collection_receipt", *args, stdout=output)
        self.assertEqual(json.loads(output.getvalue()), self.document())

    def test_profile_scope_flags_types_modes_and_region_refuse(self):
        from ingestion.collection_receipt import ReceiptError

        channel = self.channels[0]
        original = {
            "enabled": True,
            "access_approved": True,
            "kind": "imgw",
            "region": "PL",
            "config": channel.config,
            "url": channel.url,
        }
        for changed in (
            {"enabled": False},
            {"access_approved": False},
            {"kind": "gios"},
            {"region": "EU"},
            {"config": []},
            {"config": {"mode": "history"}},
            {"config": {"pagination": True}},
            {"url": channel.url.replace("https:", "http:")},
        ):
            with self.subTest(changed=tuple(changed)):
                DataChannel.objects.filter(pk=channel.pk).update(**(original | changed))
                with self.assertRaises(ReceiptError):
                    self.document()
        DataChannel.objects.filter(pk=channel.pk).update(**original)

    def test_one_internal_clock_and_two_metadata_selects_without_other_tables(self):
        from ingestion.collection_receipt import preview

        before = list(DataChannel.objects.values())
        with (
            patch("ingestion.collection_receipt.timezone.now", return_value=NOW) as clock,
            patch("ingestion.history_report.report", side_effect=AssertionError("No full report")),
            patch("ingestion.transport.Client.get", side_effect=AssertionError("No provider GET")),
            CaptureQueriesContext(connection) as queries,
        ):
            envelope = json.loads(preview(self.ids))
        clock.assert_called_once_with()
        selects = [row["sql"] for row in queries if row["sql"].lstrip().startswith("SELECT")]
        self.assertEqual(len(selects), 2)
        self.assertEqual(sum('FROM "ingestion_datachannel"' in sql for sql in selects), 1)
        self.assertEqual(sum('FROM "ingestion_collectionattempt"' in sql for sql in selects), 1)
        self.assertTrue(any("LIMIT 10001" in sql for sql in selects))
        self.assertFalse(
            any("snapshot" in sql.lower() or "payload" in sql.lower() for sql in selects)
        )
        self.assertEqual(envelope["receipt"]["as_of"], "2026-10-15T08:30:00Z")
        self.assertEqual(before, list(DataChannel.objects.values()))

    def test_reader_and_profiles_share_repeatable_read_read_only_timeout(self):
        from ingestion.collection_evidence import read_attempts

        def checked_reader(channels, **options):
            self.assertEqual([row["id"] for row in channels], self.ids)
            with connection.cursor() as cursor:
                for parameter, expected in (
                    ("transaction_isolation", "repeatable read"),
                    ("transaction_read_only", "on"),
                    ("statement_timeout", "15s"),
                ):
                    cursor.execute(f"SHOW {parameter}")
                    self.assertEqual(cursor.fetchone()[0], expected)
            return read_attempts(channels, **options)

        with patch("ingestion.collection_receipt.read_attempts", side_effect=checked_reader):
            self.document()

    def test_read_only_transaction_blocks_accidental_write_and_recovers(self):
        from ingestion.collection_receipt import ReceiptError

        def attempted_write(*args, **kwargs):
            DataChannel.objects.filter(pk=self.ids[0]).update(interval_minutes=120)
            raise AssertionError("Read-only write unexpectedly succeeded")

        with patch("ingestion.collection_receipt.read_attempts", side_effect=attempted_write):
            with self.assertRaises(ReceiptError):
                self.document()
        self.assertEqual(DataChannel.objects.get(pk=self.ids[0]).interval_minutes, 60)

    def test_existing_atomic_or_disabled_autocommit_refuses_before_queries(self):
        from ingestion.collection_receipt import ReceiptError, preview

        with transaction.atomic(), self.assertNumQueries(0), self.assertRaises(ReceiptError):
            preview(self.ids)
        with (
            patch.object(connection, "get_autocommit", return_value=False),
            self.assertNumQueries(0),
            self.assertRaises(ReceiptError),
        ):
            preview(self.ids)

    def test_naive_or_invalid_clock_refuses_before_queries(self):
        from ingestion.collection_receipt import ReceiptError, preview

        for stamp in (NOW.replace(tzinfo=None), None, "never-export-this"):
            with (
                self.subTest(stamp=type(stamp).__name__),
                patch("ingestion.collection_receipt.timezone.now", return_value=stamp),
                self.assertNumQueries(0),
                self.assertRaises(ReceiptError),
            ):
                preview(self.ids)

    def test_database_failure_is_safe_command_error_without_json(self):
        output = io.StringIO()
        with patch.object(
            DataChannel.objects, "filter", side_effect=DatabaseError("never-export-this")
        ):
            with self.assertRaises(CommandError) as error:
                call_command("collection_receipt", channel=self.ids, stdout=output)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("never-export-this", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_missing_ledger_produces_unconfirmed_without_partial_aggregates(self):
        # Schema manipulation is limited to Django's temporary test database.
        self.assertTrue(connection.settings_dict["NAME"].startswith("test_"))
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE ingestion_collectionattempt RENAME TO receipt_attempt_hidden"
            )
        try:
            receipt = self.document()["receipt"]
            self.assertEqual(receipt["evidence_status"], "unavailable")
            self.assertEqual(receipt["assessment_status"], "unconfirmed")
            self.assertEqual([row["assessment"] for row in receipt["channels"]], [None] * 3)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE receipt_attempt_hidden RENAME TO ingestion_collectionattempt"
                )
        self.assertEqual(DataChannel.objects.count(), 3)

    def test_assessment_whitelist_ignores_unknown_private_fields(self):
        from ingestion.collection_evidence import read_attempts

        def extra_fields(*args, **kwargs):
            evidence = read_attempts(*args, **kwargs)
            for assessment in evidence["acceptance_by_channel"].values():
                assessment["private_note"] = "never-export-this"
            return evidence

        with patch("ingestion.collection_receipt.read_attempts", side_effect=extra_fields):
            envelope = self.document()
        self.assertNotIn("private_note", json.dumps(envelope))
        self.assertNotIn("never-export-this", json.dumps(envelope))

    def test_failed_and_busy_are_exported_not_mistaken_for_success(self):
        for channel in self.channels:
            self.attempt(channel)
        for outcome, reason in (("failed", "collection_failed"), ("busy", "lock_busy")):
            with self.subTest(outcome=outcome):
                CollectionAttempt.objects.filter(channel=self.channels[0]).update(
                    outcome=outcome,
                    reason_code=reason,
                )
                receipt = self.document()["receipt"]
                self.assertEqual(receipt["assessment_status"], "failed")
                self.assertIn(
                    "worker_outcome_not_successful", receipt["channels"][0]["assessment"]["reasons"]
                )

    def test_manual_does_not_fill_missing_worker_proof_or_hide_failed_channel(self):
        for channel in self.channels:
            self.attempt(channel)
        CollectionAttempt.objects.filter(channel=self.channels[0]).update(
            origin="manual", batch_id=None
        )
        CollectionAttempt.objects.filter(channel=self.channels[1]).update(
            outcome="failed",
            reason_code="collection_failed",
        )
        receipt = self.document()["receipt"]
        self.assertEqual(receipt["assessment_status"], "unconfirmed")
        self.assertEqual(receipt["channels"][0]["assessment"]["worker_attempts"], 0)
        self.assertEqual(receipt["channels"][1]["assessment"]["status"], "failed")

    def test_current_interval_and_profile_change_reuse_existing_failure_criteria(self):
        for channel in self.channels:
            self.attempt(channel)
        for changed, reason in (
            ({"interval_minutes": 120}, "interval_changed"),
            (
                {"interval_minutes": 60, "config": {"private_note": "new-profile"}},
                "profile_changed",
            ),
        ):
            with self.subTest(reason=reason):
                DataChannel.objects.filter(pk=self.ids[0]).update(**changed)
                receipt = self.document()["receipt"]
                self.assertEqual(receipt["assessment_status"], "failed")
                self.assertIn(reason, receipt["channels"][0]["assessment"]["reasons"])

    def test_full_fixture_matches_reader_until_review_margin_then_refuses(self):
        from ingestion.collection_evidence import read_attempts
        from ingestion.collection_receipt import preview

        rows = []
        for channel in self.channels:
            fingerprint = configuration(channel)
            for hour in range(720):
                start = PILOT_START + timedelta(hours=hour)
                rows.append(
                    CollectionAttempt(
                        channel=channel,
                        attempt_id=uuid4(),
                        batch_id=uuid4(),
                        origin="worker",
                        started_at=start,
                        finished_at=start + timedelta(seconds=1),
                        due_at=start,
                        interval_minutes=60,
                        profile_sha256=fingerprint,
                        outcome="unchanged",
                    )
                )
        CollectionAttempt.objects.bulk_create(rows)
        for stamp, expected in (
            (PILOT_END - timedelta(microseconds=1), "pending"),
            (PILOT_END, "pass"),
            (PILOT_END + timedelta(days=5), "pass"),
            (PILOT_END + timedelta(days=5, microseconds=1), "unconfirmed"),
        ):
            with (
                self.subTest(stamp=stamp),
                patch("ingestion.collection_receipt.timezone.now", return_value=stamp),
            ):
                output = preview(self.ids)
                receipt = json.loads(output)["receipt"]
                self.assertEqual(receipt["assessment_status"], expected)
                values = list(
                    DataChannel.objects.order_by("pk").values(
                        "id",
                        "slug",
                        "kind",
                        "region",
                        "url",
                        "config",
                        "interval_minutes",
                    )
                )
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                    evidence = read_attempts(values, now=stamp, imgw_pilot_window=True)
                for channel in receipt["channels"]:
                    expected_assessment = evidence["acceptance_by_channel"][
                        channel["channel_id"]
                    ].copy()
                    for field in ("window_start", "window_end", "observed_until"):
                        expected_assessment[field] = (
                            expected_assessment[field].isoformat().replace("+00:00", "Z")
                        )
                    self.assertEqual(channel["assessment"], expected_assessment)

    def test_inconsistent_or_premature_reader_confirmation_is_refused(self):
        from ingestion.collection_evidence import read_attempts
        from ingestion.collection_receipt import ReceiptError

        def premature(*args, **kwargs):
            evidence = read_attempts(*args, **kwargs)
            for assessment in evidence["acceptance_by_channel"].values():
                assessment.update(status="pass", continuity_confirmed=True, window_complete=True)
            return evidence

        with patch("ingestion.collection_receipt.read_attempts", side_effect=premature):
            with self.assertRaises(ReceiptError):
                self.document()

    def test_as_of_preserves_microsecond_after_retention_deadline(self):
        from ingestion.collection_receipt import preview

        stamp = PILOT_END + timedelta(days=5, microseconds=1)
        with patch("ingestion.collection_receipt.timezone.now", return_value=stamp):
            receipt = json.loads(preview(self.ids))["receipt"]
        self.assertEqual(receipt["as_of"], "2026-11-19T08:00:00.000001Z")
        self.assertEqual(receipt["assessment_status"], "unconfirmed")
        self.assertIn("window_start_not_retained", receipt["channels"][0]["assessment"]["reasons"])

    def test_observed_until_preserves_actual_microsecond_clock(self):
        from ingestion.collection_receipt import preview

        stamp = NOW + timedelta(microseconds=123456)
        with patch("ingestion.collection_receipt.timezone.now", return_value=stamp):
            receipt = json.loads(preview(self.ids))["receipt"]
        self.assertEqual(receipt["as_of"], "2026-10-15T08:30:00.123456Z")
        for channel in receipt["channels"]:
            self.assertEqual(channel["assessment"]["observed_until"], receipt["as_of"])
