"""Thread, fd and logger cleanup for REACHER.shutdown() and reset()."""

import logging
import os
import threading
import time
import uuid

from reacher.kernel.reacher import REACHER
from reacher.session_manager import SessionManager

TOLERANCE = 2


def _fd_count():
    return len(os.listdir("/proc/self/fd"))


def _settle(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.05)
    return predicate()


def _monitor_threads():
    return [t for t in threading.enumerate() if t.name == "monitor_time_limit" and t.is_alive()]


def test_destroy_cycles_return_threads_and_fds_to_baseline(monkeypatch):
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda s: real_sleep(min(s, 0.01)))
    sm = SessionManager()
    base_threads = threading.active_count()
    base_fds = _fd_count()

    for _ in range(30):
        sid = sm.create_session("SIMULATOR", "fr")
        instance = sm.get_instance(sid)
        instance.set_COM_port("SIMULATOR")
        instance.open_serial()
        sm.destroy_session(sid)

    assert _settle(lambda: threading.active_count() <= base_threads + TOLERANCE)
    assert threading.active_count() <= base_threads + TOLERANCE
    assert _fd_count() <= base_fds + TOLERANCE


def test_shutdown_is_idempotent_on_unconnected_instance():
    instance = REACHER(session_id=uuid.uuid4().hex)
    instance.shutdown()
    instance.shutdown()


def test_shutdown_from_own_time_check_thread_does_not_raise(caplog):
    instance = REACHER(session_id=uuid.uuid4().hex)
    errors = []

    def worker():
        instance.time_check_thread = threading.current_thread()
        try:
            instance.shutdown(timeout=1.0)
        except RuntimeError as exc:  # pragma: no cover - failure path
            errors.append(exc)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=10)

    assert not t.is_alive()
    assert errors == []
    assert "Error during shutdown" not in caplog.text


def test_shutdown_releases_log_handlers_and_logger():
    session_id = uuid.uuid4().hex
    instance = REACHER(session_id=session_id)
    name = instance.logger.name
    assert instance.logger.handlers
    assert name in logging.Logger.manager.loggerDict

    instance.shutdown()

    assert instance.logger.handlers == []
    assert name not in logging.Logger.manager.loggerDict


def test_reset_leaves_at_most_one_monitor_thread(monkeypatch):
    base_monitors = len(_monitor_threads())
    instance = REACHER(session_id=uuid.uuid4().hex)
    instance.set_COM_port("SIMULATOR")
    instance.open_serial()
    try:
        for _ in range(5):
            instance.reset()
        assert len(_monitor_threads()) - base_monitors <= 1
    finally:
        instance.shutdown()
