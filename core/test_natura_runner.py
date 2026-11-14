import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from core import natura_runner as runner


class NaturaRunnerTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.lock = Path(self.directory.name) / "slot"
        self.lock_patch = patch.object(runner, "LOCK_PATH", self.lock)
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.children = []
        self.addCleanup(self.reap_children)

    def reap_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=2)
            if child.stdout is not None:
                child.stdout.close()

    def run_child(self, code, job=7):
        real_popen = subprocess.Popen

        def spawn(argv, **kwargs):
            self.assertEqual(
                argv,
                [sys.executable, str(runner.MANAGE_PATH), "natura_sites_report", "--job", str(job)],
            )
            self.assertFalse(kwargs["shell"])
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual(len(kwargs["pass_fds"]), 1)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertNotIn("preexec_fn", kwargs)
            child = real_popen([sys.executable, "-c", code], **kwargs)
            self.children.append(child)
            return child

        with patch.object(runner.subprocess, "Popen", side_effect=spawn):
            return runner.run_report(job)

    def assert_refused(self, code, expected):
        with self.assertRaises(runner.ReportRefused) as caught:
            self.run_child(code)
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)
        self.assertTrue(all(child.poll() is not None for child in self.children))

    def test_success_fixed_command_and_csv_limit_isolated(self):
        previous = csv.field_size_limit()
        result = self.run_child(
            "import csv,json; csv.field_size_limit(1048576); "
            "print(json.dumps({'job_id':7,'records':1,'raw':'zażółć'}))"
        )
        self.assertEqual(result, {"job_id": 7, "records": 1, "raw": "zażółć"})
        self.assertEqual(csv.field_size_limit(), previous)
        self.assertEqual(self.lock.stat().st_size, 0)

    def test_invalid_ids_never_spawn_or_open_slot(self):
        for value in (None, True, 0, -1, "7", 2**63, 7.0):
            with self.subTest(value=value), patch.object(runner.subprocess, "Popen") as spawn:
                with self.assertRaises(ValueError):
                    runner.run_report(value)
                spawn.assert_not_called()
                self.assertFalse(self.lock.exists())

    def test_duplicate_nan_truncated_and_wrong_job_json_refused(self):
        for raw in (
            '{"job_id":7,"job_id":7}',
            '{"job_id":7,"n":NaN}',
            '{"job_id":7,"n":1e400}',
            '{"job_id":7',
            "[]",
            '{"job_id":true}',
            '{"job_id":8}',
            "{}",
        ):
            with self.subTest(raw=raw):
                self.assert_refused(f"print({json.dumps(raw)})", "invalid")

    def test_nonzero_and_bad_utf8_never_return_partial_result(self):
        self.assert_refused("print('{\"job_id\":7}'); raise SystemExit(1)", "invalid")
        self.assert_refused("import os; os.write(1,b'\\xff')", "invalid")

    def test_budget_exact_limit_passes_one_more_byte_refused(self):
        raw = json.dumps({"job_id": 7}).encode()
        with patch.object(runner, "MAX_OUTPUT_BYTES", len(raw)):
            self.assertEqual(self.run_child(f"import os; os.write(1,{raw!r})"), {"job_id": 7})
            self.assert_refused(f"import os; os.write(1,{raw!r}+b' ')", "invalid")

    def test_output_flood_is_bounded_and_child_reaped(self):
        self.assert_refused("import os; block=b'x'*8192\nwhile True: os.write(1,block)", "invalid")
        with runner._slot():
            pass

    def test_silent_and_stdout_closed_children_obey_deadline(self):
        for prefix in ("", "os.close(1); "):
            with self.subTest(prefix=prefix), patch.object(runner, "DEADLINE_SECONDS", 0.15):
                self.assert_refused(f"import os,time; {prefix}time.sleep(60)", "timeout")
        with runner._slot():
            pass

    def test_ignored_term_is_killed_reaped_and_slot_inode_preserved(self):
        with patch.object(runner, "DEADLINE_SECONDS", 0.3):
            self.assert_refused(
                "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "print('ready',flush=True); time.sleep(60)",
                "timeout",
            )
        inode = self.lock.stat().st_ino
        with runner._slot():
            self.assertEqual(self.lock.stat().st_ino, inode)

    def test_busy_slot_refuses_without_spawn(self):
        with runner._slot(), patch.object(runner.subprocess, "Popen") as spawn:
            with self.assertRaises(runner.ReportRefused) as caught:
                runner.run_report(7)
            self.assertEqual(caught.exception.code, "busy")
            spawn.assert_not_called()

    def test_different_process_cannot_acquire_inherited_slot(self):
        code = (
            "from pathlib import Path; from core import natura_runner as r; "
            f"r.LOCK_PATH=Path({str(self.lock)!r})\n"
            "try:\n with r._slot(): raise SystemExit(9)\n"
            "except r.ReportRefused as error:\n"
            " assert error.code=='busy'\n print('{\"job_id\":7}')"
        )
        self.assertEqual(self.run_child(code), {"job_id": 7})

    def test_symlink_bad_permissions_nonempty_fifo_and_hardlink_refused(self):
        target = Path(self.directory.name) / "target"
        target.touch(mode=0o600)
        for kind in ("symlink", "permissions", "nonempty", "fifo", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.lock.symlink_to(target)
                elif kind == "fifo":
                    os.mkfifo(self.lock, 0o600)
                elif kind == "hardlink":
                    os.link(target, self.lock)
                else:
                    self.lock.touch(mode=0o644 if kind == "permissions" else 0o600)
                    if kind == "nonempty":
                        self.lock.write_bytes(b"x")
                with patch.object(runner.subprocess, "Popen") as spawn:
                    with self.assertRaises(runner.ReportRefused) as caught:
                        runner.run_report(7)
                    self.assertEqual(caught.exception.code, "unavailable")
                    spawn.assert_not_called()
                self.lock.unlink()

    def test_platform_and_spawn_failure_refuse_without_leaking_details(self):
        with patch.object(runner, "fcntl", None), patch.object(runner.subprocess, "Popen") as p:
            with self.assertRaises(runner.ReportRefused) as caught:
                runner.run_report(7)
            self.assertEqual(str(caught.exception), "unavailable")
            p.assert_not_called()
        with patch.object(runner.subprocess, "Popen", side_effect=OSError("private detail")):
            with self.assertRaises(runner.ReportRefused) as caught:
                runner.run_report(7)
            self.assertEqual(str(caught.exception), "unavailable")
        with runner._slot():
            pass

    def test_only_slot_fd_inherited_not_other_open_fd(self):
        with tempfile.TemporaryFile() as other:
            os.set_inheritable(other.fileno(), True)
            code = (
                f"import os\ntry: os.fstat({other.fileno()}); raise SystemExit(9)\n"
                "except OSError: print('{\"job_id\":7}')"
            )
            self.assertEqual(self.run_child(code), {"job_id": 7})

    def test_child_holds_slot_after_parent_fd_close_until_exit(self):
        with runner._slot() as fd:
            inode = self.lock.stat().st_ino
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(fd,),
            )
            self.children.append(child)
        try:
            with self.assertRaises(runner.ReportRefused) as caught:
                with runner._slot():
                    pass
            self.assertEqual(caught.exception.code, "busy")
        finally:
            child.stdin.close()
            child.wait(timeout=2)
        with runner._slot():
            self.assertEqual(self.lock.stat().st_ino, inode)

    def test_lock_other_owner_and_replaced_inode_refuse(self):
        real_fstat = os.fstat

        def wrong_owner(fd):
            value = list(real_fstat(fd))
            value[4] += 1
            return os.stat_result(value)

        with patch.object(runner.os, "fstat", side_effect=wrong_owner):
            with self.assertRaises(runner.ReportRefused) as caught:
                runner.run_report(7)
            self.assertEqual(caught.exception.code, "unavailable")

        real_stat = os.stat

        def replaced(path, **kwargs):
            value = list(real_stat(path, **kwargs))
            value[1] += 1
            return os.stat_result(value)

        with patch.object(runner.os, "stat", side_effect=replaced):
            with self.assertRaises(runner.ReportRefused) as caught:
                runner.run_report(7)
            self.assertEqual(caught.exception.code, "unavailable")

    def test_deadline_starts_before_spawn_returns(self):
        real_popen = subprocess.Popen

        def delayed(argv, **kwargs):
            child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            self.children.append(child)
            return child

        with (
            patch.object(runner.subprocess, "Popen", side_effect=delayed),
            patch.object(runner.time, "monotonic", side_effect=[0, 16]),
        ):
            with self.assertRaises(runner.ReportRefused) as caught:
                runner.run_report(7)
            self.assertEqual(caught.exception.code, "timeout")
        self.assertTrue(all(child.poll() is not None for child in self.children))

    def test_signal_exit_race_still_waits_and_closes_stdout(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "pass"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        self.children.append(child)
        child.wait(timeout=2)
        with (
            patch.object(child, "poll", return_value=None),
            patch.object(child, "terminate", side_effect=ProcessLookupError),
            patch.object(child, "wait", wraps=child.wait) as wait,
        ):
            runner._reap(child)
            wait.assert_called_once()
        self.assertTrue(child.stdout.closed)
