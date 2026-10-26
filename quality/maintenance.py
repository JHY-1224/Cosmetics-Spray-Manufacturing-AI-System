"""Cooperating runtime barrier for consistent database/object/checkpoint maintenance.

Both a process-wide reader count and an OS lock are necessary: POSIX file locks
alone do not isolate concurrent threads in the same API/worker process.
"""

import contextvars
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import fasteners


class MaintenanceBusy(RuntimeError):
    pass


_held = contextvars.ContextVar("quality_maintenance_locks", default={})
_guards = {}
_registry = threading.Lock()


class _Guard:
    def __init__(self, path):
        self.condition = threading.Condition()
        self.readers = self.waiters = 0
        self.writer = False
        self.file = fasteners.InterProcessReaderWriterLock(str(path / "maintenance.lock"))


def guard(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _registry:
        return str(path), _guards.setdefault(str(path), _Guard(path))


@contextmanager
def gate(path, exclusive=False, timeout=30):
    key, item = guard(path)
    if key in _held.get():
        if exclusive and _held.get()[key] != "exclusive":
            raise MaintenanceBusy("维护须通过独立运维入口执行，不能从正在办理的业务事务升级锁")
        yield
        return
    deadline, acquired = time.monotonic() + timeout, False
    with item.condition:
        if exclusive:
            item.waiters += 1
        try:
            while item.writer or (item.readers if exclusive else item.waiters):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise MaintenanceBusy("等待当前业务或备份维护超时，未执行维护或业务写入")
                item.condition.wait(left)
            if exclusive:
                item.writer = True
            if exclusive or not item.readers:
                function = item.file.acquire_write_lock if exclusive else item.file.acquire_read_lock
                if not function(timeout=max(0, deadline - time.monotonic())):
                    raise MaintenanceBusy("另一进程正在执行维护或办理业务，请稍后重试")
            if not exclusive:
                item.readers += 1
            acquired = True
        finally:
            if exclusive:
                item.waiters -= 1
                if not acquired:
                    item.writer = False
                    item.condition.notify_all()
    token = _held.set({**_held.get(), key: "exclusive" if exclusive else "read"})
    try:
        yield
    finally:
        try:
            _held.reset(token)
        except ValueError:
            # FastAPI can enter/exit a synchronous yield dependency in separate
            # copied AnyIO contexts. The lock itself is guarded independently.
            pass
        with item.condition:
            if exclusive:
                item.file.release_write_lock()
                item.writer = False
            else:
                item.readers -= 1
                if not item.readers:
                    item.file.release_read_lock()
            item.condition.notify_all()
