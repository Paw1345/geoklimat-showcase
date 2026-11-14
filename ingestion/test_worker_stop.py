import os
import signal
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from ingestion.collection_evidence import _worker_attempt_scope, begin_attempt
from ingestion.worker_stop import STOP

WORKER = "ingestion.management.commands.ingestion_worker."


class WorkerStopTests(SimpleTestCase):
    def setUp(self):
        handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in handlers.items()])
        self.addCleanup(STOP.clear)
        # The advisory-lock proof needs PostgreSQL; see test_marine_profile_apply.
        presence = patch(WORKER + "WorkerPresence")
        presence.start().return_value.ensure.return_value = True
        self.addCleanup(presence.stop)

    def test_sigterm_during_cycle_skips_prune_and_ends_loop(self):
        commands = []

        def wait(timeout):
            if not STOP.is_set():
                raise AssertionError("signal did not reach the stop request")
            return True

        def downloads():
            os.kill(os.getpid(), signal.SIGTERM)
            return []

        with (
            patch(WORKER + "call_command", lambda name, **_: commands.append(name)),
            patch(WORKER + "run_downloads", downloads),
            patch(WORKER + "run_data", return_value=[]) as data,
            patch(WORKER + "run_batch", return_value=[]) as batch,
            patch.object(STOP, "wait", wait),
        ):
            call_command("ingestion_worker")
        self.assertTrue(STOP.is_set())
        self.assertEqual(commands, ["collect_alerts"])
        # Their own loops check the stop request before the first unit of work.
        data.assert_called_once()
        batch.assert_called_once()

    def test_once_clears_previous_stop_request(self):
        STOP.set()
        with (
            patch(WORKER + "call_command"),
            patch(WORKER + "run_downloads", return_value=[]),
            patch(WORKER + "run_data", return_value=[]) as data,
            patch(WORKER + "run_batch", return_value=[]),
        ):
            call_command("ingestion_worker", once=True)
        data.assert_called_once()
        self.assertFalse(STOP.is_set())

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_worker_scope_is_data_only_and_resets_between_cycles(self):
        from types import SimpleNamespace

        channel = SimpleNamespace(
            pk=1,
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/synop",
            config={},
            next_fetch_at=None,
            interval_minutes=60,
        )
        captures = []

        def capture():
            return begin_attempt(channel, dry_run=False).values

        def outside(*args, **kwargs):
            self.assertEqual(capture()["origin"], "unknown")
            return []

        def data():
            captures.append(capture())
            return []

        with (
            patch(WORKER + "call_command", outside),
            patch(WORKER + "run_downloads", outside),
            patch(WORKER + "run_data", data),
            patch(WORKER + "run_batch", outside),
        ):
            call_command("ingestion_worker", once=True)
            call_command("ingestion_worker", once=True)
        self.assertEqual([row["origin"] for row in captures], ["worker", "worker"])
        self.assertIsNotNone(captures[0]["batch_id"])
        self.assertNotEqual(captures[0]["batch_id"], captures[1]["batch_id"])
        self.assertEqual(capture()["origin"], "unknown")

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_worker_exception_does_not_leak_origin(self):
        from types import SimpleNamespace

        channel = SimpleNamespace(
            pk=1,
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/synop",
            config={},
            next_fetch_at=None,
            interval_minutes=60,
        )
        with (
            patch(WORKER + "call_command"),
            patch(WORKER + "run_downloads", return_value=[]),
            patch(WORKER + "run_data", side_effect=RuntimeError("fixture")),
            self.assertLogs("ingestion.management.commands.ingestion_worker", level="ERROR"),
            self.assertRaises(RuntimeError),
        ):
            call_command("ingestion_worker", once=True)
        self.assertEqual(begin_attempt(channel, dry_run=False).values["origin"], "unknown")

    @override_settings(COLLECTION_EVIDENCE_ENABLED=False)
    def test_off_preserves_order_and_news_fairness_without_batch_uuid(self):
        order = []

        def step(name, result=None):
            def run(*args, **kwargs):
                order.append(name)
                if name == "news":
                    self.assertEqual(kwargs, {"limit": 5})
                return result

            return run

        with (
            patch(
                "ingestion.collection_evidence.uuid4", side_effect=AssertionError("OFF UUID")
            ) as allocate,
            patch(WORKER + "call_command", lambda name, **_: order.append(name)),
            patch(WORKER + "run_downloads", step("downloads", [])),
            patch(WORKER + "run_data", step("data", [])),
            patch(WORKER + "run_batch", step("news", [])),
        ):
            call_command("ingestion_worker", once=True)
            allocate.assert_not_called()
        self.assertEqual(order, ["collect_alerts", "downloads", "prune_data", "data", "news"])

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_stopped_scope_does_not_allocate_a_batch_or_query(self):
        STOP.set()
        with patch(
            "ingestion.collection_evidence.uuid4", side_effect=AssertionError("stopped UUID")
        ) as allocate:
            with _worker_attempt_scope():
                self.assertTrue(STOP.is_set())
            allocate.assert_not_called()

    @override_settings(COLLECTION_EVIDENCE_ENABLED=True)
    def test_batch_setup_failure_still_runs_data_without_worker_origin(self):
        from types import SimpleNamespace
        from uuid import uuid4

        channel = SimpleNamespace(
            pk=1,
            kind="imgw",
            region="PL",
            url="https://danepubliczne.imgw.pl/api/data/synop",
            config={},
            next_fetch_at=None,
            interval_minutes=60,
        )
        captures = []

        def data():
            captures.append(begin_attempt(channel, dry_run=False).values)
            return []

        with (
            patch(
                "ingestion.collection_evidence.uuid4",
                side_effect=[RuntimeError("fixture"), uuid4()],
            ),
            patch(WORKER + "call_command"),
            patch(WORKER + "run_downloads", return_value=[]),
            patch(WORKER + "run_data", data),
            patch(WORKER + "run_batch", return_value=[]),
            self.assertLogs("ingestion.collection_evidence", level="WARNING") as logs,
        ):
            call_command("ingestion_worker", once=True)
        self.assertEqual(captures[0]["origin"], "unknown")
        self.assertIsNone(captures[0]["batch_id"])
        self.assertEqual(
            logs.output,
            [
                "WARNING:ingestion.collection_evidence:collection_evidence_worker_context_unavailable"
            ],
        )
