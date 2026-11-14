"""Bounded, Linux-container execution of the existing read-only Natura CLI.

Internal boundary only: callers must authorize metadata before execution and
validate the full report schema/provenance in a fresh snapshot before rendering.
"""

import json
import math
import os
import selectors
import stat
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

try:
    import fcntl
except ImportError:
    fcntl = None

MANAGE_PATH = Path(__file__).resolve().parents[1] / "manage.py"
LOCK_PATH = Path("/tmp/geoklimat-natura-report.lock")
DEADLINE_SECONDS = 15
MAX_OUTPUT_BYTES = 128 * 1024


class ReportRefused(Exception):
    """Only a fixed refusal code, never child output or environment details."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@contextmanager
def _slot():
    if fcntl is None or os.name != "posix":
        raise ReportRefused("unavailable")
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != 0
            or info.st_nlink != 1
        ):
            raise ReportRefused("unavailable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReportRefused("busy") from error
        current = os.stat(LOCK_PATH, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ReportRefused("unavailable")
        yield fd
    finally:
        # No LOCK_UN: a live child shares this open-file description after
        # parent loss. No unlink: every contender must lock the same inode.
        os.close(fd)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON constant")


def _decode(body, job_id):
    report = json.loads(body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    if type(report) is not dict or type(report.get("job_id")) is not int:
        raise ValueError("report object required")
    if report["job_id"] != job_id:
        raise ValueError("report job differs")
    pending = [report]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite JSON number")
    return report


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReportRefused("timeout")
    return remaining


def _collect(child, deadline):
    body = bytearray()
    fd = child.stdout.fileno()
    os.set_blocking(fd, False)
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            if not selector.select(_remaining(deadline)):
                raise ReportRefused("timeout")
            try:
                block = os.read(fd, min(8192, MAX_OUTPUT_BYTES + 1 - len(body)))
            except BlockingIOError:
                continue
            if not block:
                break
            body.extend(block)
            if len(body) > MAX_OUTPUT_BYTES:
                raise ReportRefused("invalid")
    child.wait(timeout=_remaining(deadline))
    if child.returncode != 0:
        raise ReportRefused("invalid")
    return body


def _reap(child):
    try:
        if child.poll() is None:
            with suppress(ProcessLookupError):
                child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    child.kill()
                child.wait(timeout=1)
        else:
            child.wait(timeout=1)
    finally:
        child.stdout.close()


def run_report(job_id):
    """Return structurally strict JSON or refuse; no DB/HTTP access in parent.

    No report/schema/source authorization is implied by a successful return.
    Limits are fixed; callers cannot supply commands, files or execution options.
    """
    if type(job_id) is not int or not 0 < job_id < 2**63:
        raise ValueError("Natura job must be a positive int64")
    try:
        with _slot() as slot_fd:
            deadline = time.monotonic() + DEADLINE_SECONDS
            child = subprocess.Popen(
                [sys.executable, str(MANAGE_PATH), "natura_sites_report", "--job", str(job_id)],
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                pass_fds=(slot_fd,),
                bufsize=0,
            )
            try:
                body = _collect(child, deadline)
            finally:
                _reap(child)
        try:
            return _decode(body, job_id)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ReportRefused("invalid") from error
    except subprocess.TimeoutExpired as error:
        raise ReportRefused("timeout") from error
    except OSError as error:
        raise ReportRefused("unavailable") from error
