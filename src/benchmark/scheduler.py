"""Worker subprocess lifecycle and bounded execution telemetry."""

from contextlib import contextmanager
import os
import subprocess
import sys
import time

from src.infrastructure.execution import numeric_usage
from src.persistence import benchmarks as store
from src.persistence.storage import PROJECT_ROOT


class BudgetReached(BaseException):
    pass


class Monitor:
    def __init__(self, attempt_id, budget):
        self.attempt_id = attempt_id
        self.budget = budget

    def begin(self, role, phase):
        call_id = store.begin_call(self.attempt_id, role, phase, self.budget)
        if call_id is None:
            raise BudgetReached
        return call_id

    def end(self, call_id, started, status, usage):
        store.end_call(call_id, time.monotonic() - started, status, numeric_usage(usage))


def run_once():
    item = store.dispatch()
    if item is None:
        return False
    attempt_id, timeout = item
    attempt = store.get_attempt(attempt_id)
    try:
        # sys.executable is the existing uv environment. The child never
        # claims unrelated work or starts a long-running worker.
        with subprocess.Popen([sys.executable, "-m", "apps.worker", "--benchmark-attempt", str(attempt_id), str(os.getpid())],
                              cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL) as child:
            try:
                deadline = time.monotonic() + timeout
                while True:
                    remaining = deadline - time.monotonic()
                    code = child.poll()
                    if code is not None:
                        break
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(child.args, timeout)
                    try:
                        code = child.wait(timeout=min(remaining, 3))
                        break
                    except subprocess.TimeoutExpired:
                        store.heartbeat(attempt_id)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                child.kill()
                child.wait()
                raise
    except subprocess.TimeoutExpired:
        store.finish(attempt_id, error="timeout")
    except KeyboardInterrupt:
        store.finish(attempt_id, interrupted=True, error="worker_interrupted")
        raise
    except OSError:
        store.finish(attempt_id, error="worker_launch_failed")
    else:
        if code == 75:
            store.finish(attempt_id, interrupted=True, error="budget_reached")
        elif code:
            store.finish(attempt_id, error="worker_failed")
        elif attempt["phase"] == "generate":
            store.finish(attempt_id)
        else:
            # A healthy scoring child commits its own result before it exits.
            store.finish(attempt_id, error="missing_score")
    return True


@contextmanager
def child_lifetime(parent_pid):
    """Stop a bounded child if its supervisor process disappears."""
    from os import getppid
    from os import _exit
    from threading import Event, Thread

    def watch():
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            handle = kernel.OpenProcess(0x00100000, False, parent_pid)
            if not handle:
                _exit(1)
            try:
                while not done.is_set():
                    if kernel.WaitForSingleObject(handle, 250) == 0:
                        _exit(1)
            finally:
                kernel.CloseHandle(handle)
        else:
            while not done.wait(0.25):
                if getppid() != parent_pid:
                    _exit(1)

    done = Event()
    thread = Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
