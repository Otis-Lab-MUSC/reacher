"""Shared fixtures.

The critical one is ``_isolate_diagnostic_logs``: without it every test run
would append to the developer's real ``~/REACHER/LOG/runs`` and leave a live
writer thread behind between tests.
"""

import pytest

from reacher import diagnostics


@pytest.fixture(autouse=True)
def _isolate_diagnostic_logs(tmp_path, monkeypatch):
    """Point diagnostic logging at a per-test temp dir and tear it down after.

    Autouse because logging is configured as a side effect of the FastAPI
    lifespan, so any test spinning up a TestClient would otherwise touch the
    real home directory without ever mentioning logging.
    """
    monkeypatch.setenv("REACHER_LOG_DIR", str(tmp_path / "runs"))
    diagnostics.reset_for_tests()
    yield
    diagnostics.reset_for_tests()


@pytest.fixture
def diag_sink(tmp_path):
    """A started sink writing under ``tmp_path``, stopped on teardown."""
    sink = diagnostics.configure_logging(root=str(tmp_path / "runs"), prune=False)
    yield sink
    diagnostics.reset_for_tests()


@pytest.fixture
def read_records(diag_sink):
    """Return a callable that drains the sink and parses its records."""
    import json

    def _read():
        diag_sink.stop()
        try:
            with open(diag_sink.path, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []

    return _read
