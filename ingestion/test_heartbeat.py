from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from ingestion import heartbeat

URL = "https://hc.example.org/ping/token"


class HeartbeatTests(SimpleTestCase):
    def setUp(self):
        heartbeat._last_sent = None
        self.addCleanup(setattr, heartbeat, "_last_sent", None)

    def test_no_address_means_no_request(self):
        with override_settings(WORKER_HEARTBEAT_URL=""), patch.object(heartbeat, "_open") as opened:
            self.assertFalse(heartbeat.ping(now=0))
        opened.assert_not_called()

    @override_settings(WORKER_HEARTBEAT_URL=URL)
    def test_sends_once_per_interval(self):
        with patch.object(heartbeat, "_open", return_value=200) as opened:
            self.assertTrue(heartbeat.ping(now=0))
            self.assertFalse(heartbeat.ping(now=heartbeat.MIN_INTERVAL_SECONDS - 1))
            self.assertTrue(heartbeat.ping(now=heartbeat.MIN_INTERVAL_SECONDS))
        self.assertEqual(opened.call_count, 2)
        opened.assert_called_with(URL)

    def test_rejects_plain_http_and_credentials(self):
        for url in [
            "http://hc.example.org/ping",
            "https://user:pass@hc.example.org/ping",
            "ftp://x",
        ]:
            with (
                self.subTest(url=url),
                override_settings(WORKER_HEARTBEAT_URL=url),
                patch.object(heartbeat, "_open") as opened,
                self.assertLogs("ingestion.heartbeat", "ERROR"),
            ):
                self.assertFalse(heartbeat.ping(now=0))
                opened.assert_not_called()

    @override_settings(WORKER_HEARTBEAT_URL=URL)
    def test_failure_is_logged_without_the_address(self):
        with (
            patch.object(heartbeat, "_open", side_effect=OSError(URL)),
            self.assertLogs("ingestion.heartbeat", "WARNING") as logs,
        ):
            self.assertFalse(heartbeat.ping(now=0))
        self.assertNotIn("token", "".join(logs.output))
        self.assertIn("OSError", logs.output[0])


class WorkerHeartbeatTests(SimpleTestCase):
    def cycle(self, **batch):
        target = "ingestion.management.commands.ingestion_worker."
        with (
            patch(target + "WorkerPresence") as presence,
            patch(target + "call_command"),
            patch(target + "run_downloads", return_value=[]),
            patch(target + "run_data", return_value=[]),
            patch(target + "run_batch", **batch),
            patch(target + "ping") as ping,
        ):
            presence.return_value.ensure.return_value = True
            try:
                call_command("ingestion_worker", once=True)
            except RuntimeError:
                pass
        return ping

    def test_worker_pings_after_a_clean_cycle(self):
        self.cycle(return_value=[]).assert_called_once_with()

    def test_worker_does_not_ping_after_a_failed_cycle(self):
        with self.assertLogs("ingestion.management.commands.ingestion_worker", "ERROR"):
            self.cycle(side_effect=RuntimeError("boom")).assert_not_called()
