"""Tests for the diagnostic logging system (``reacher.diagnostics``)."""

import json
import logging
import os
import threading
import time

import pytest

from reacher import diagnostics
from reacher.diagnostics import bridge, context, redact
from reacher.diagnostics.schema import TIER_APP, TIER_WIRE, LogRecord, level_name
from reacher.diagnostics.sink import LogSink, prune_runs


def _drain(sink) -> list[dict]:
    sink.stop()
    with open(sink.path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestRedaction:
    def test_secret_keys_are_replaced(self):
        out = redact.redact({"REACHER_API_KEY": "abc", "ws_token": "t", "password": "p"})
        assert all(v == redact.REDACTED for v in out.values())

    def test_ordinary_values_are_kept_verbatim(self):
        """The project decision is verbatim values — subject IDs and paths are
        exactly what makes a bug report reproducible."""
        out = redact.redact({"subject": "M12", "dose": 0.5, "dest": "/home/lab/data"})
        assert out == {"subject": "M12", "dose": 0.5, "dest": "/home/lab/data"}

    def test_nested_secrets_are_reached(self):
        out = redact.redact({"outer": {"inner": {"api_key": "x"}}})
        assert out["outer"]["inner"]["api_key"] == redact.REDACTED

    def test_long_strings_truncated_not_dropped(self):
        out = redact.redact({"blob": "x" * (redact.MAX_STR + 500)})
        assert len(out["blob"]) < redact.MAX_STR + 100
        assert out["blob"].startswith("xxx")

    def test_env_snapshot_limited_to_reacher_vars(self):
        out = redact.redact_env({"REACHER_PORT": "6229", "REACHER_API_KEY": "k", "AWS_SECRET": "s"})
        assert out == {"REACHER_PORT": "6229", "REACHER_API_KEY": redact.REDACTED}

    def test_unrepresentable_object_does_not_raise(self):
        class Bad:
            def __repr__(self):
                raise ValueError("boom")

        assert redact.redact({"b": Bad()}) is not None


class TestSchema:
    def test_level_mapping(self):
        assert level_name(logging.WARNING) == "warn"
        assert level_name(logging.CRITICAL) == "fatal"
        assert level_name(5) == "debug"

    def test_seq_is_monotonic(self):
        a = LogRecord(evt="a", tier=TIER_APP).finalize()
        b = LogRecord(evt="b", tier=TIER_APP).finalize()
        assert b.seq > a.seq

    def test_empty_optionals_are_omitted(self):
        """Absent beats null at millions of lines, and `jq` treats them alike."""
        out = LogRecord(evt="a", tier=TIER_APP).finalize().to_dict()
        assert "msg" not in out and "data" not in out and "session_id" not in out

    def test_serialization_never_raises(self):
        class Bad:
            def __repr__(self):
                raise ValueError("boom")

        payload = json.loads(LogRecord(evt="a", tier=TIER_APP, data={"x": Bad()}).finalize().to_json())
        assert payload["evt"] in ("a", "log.serialize_failed")


class TestContext:
    def test_bind_scopes_and_restores(self):
        assert context.get_corr_id() is None
        with context.bind() as cid:
            assert context.get_corr_id() == cid
        assert context.get_corr_id() is None

    def test_bind_restores_on_exception(self):
        with pytest.raises(RuntimeError):
            with context.bind():
                raise RuntimeError("x")
        assert context.get_corr_id() is None

    def test_nested_bind_inherits_outer_corr_id(self):
        with context.bind() as outer:
            with context.bind() as inner:
                assert inner == outer

    async def test_corr_id_reaches_fastapi_sync_endpoints(self):
        """The load-bearing propagation guarantee: FastAPI runs sync endpoints
        on an anyio worker thread that *copies* the context, so a corr_id bound
        in middleware is visible to the endpoint and anything it calls."""
        import anyio.to_thread

        seen = []
        with context.bind(corr_id="fixed123"):
            await anyio.to_thread.run_sync(lambda: seen.append(context.get_corr_id()))
        assert seen == ["fixed123"]

    def test_corr_id_does_not_leak_into_daemon_threads(self):
        """The documented limit of correlation.  A raw threading.Thread does
        not inherit context, so the kernel's long-lived serial-RX thread cannot
        carry a corr_id — RX records are correlated by session_id and time
        instead.  Asserted so the boundary is not silently "fixed" later."""
        seen = []
        with context.bind(corr_id="fixed123"):
            t = threading.Thread(target=lambda: seen.append(context.get_corr_id()))
            t.start()
            t.join()
        assert seen == [None]


class TestSink:
    def test_roundtrip(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        sink.emit(LogRecord(evt="serial.rx", tier=TIER_WIRE, data={"line": "{}"}))
        records = _drain(sink)
        assert records[0]["evt"] == "serial.rx"
        assert records[0]["tier"] == TIER_WIRE
        assert records[0]["run_id"] == context.RUN_ID

    def test_no_records_lost_under_load(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        for i in range(5000):
            sink.emit(LogRecord(evt="t", tier=TIER_APP, data={"i": i}))
        records = _drain(sink)
        assert len(records) == 5000
        assert sink.stats()["dropped"] == 0

    def test_records_keep_emission_order(self, tmp_path):
        """seq is stamped by the producer, so ordering reflects when events
        happened rather than when the writer got to them."""
        sink = LogSink(root=str(tmp_path)).start()
        for i in range(200):
            sink.emit(LogRecord(evt="t", tier=TIER_APP, data={"i": i}))
        records = _drain(sink)
        assert [r["data"]["i"] for r in records] == list(range(200))
        assert all(a["seq"] < b["seq"] for a, b in zip(records, records[1:]))

    def test_rotation_creates_backups_and_bounds_total(self, tmp_path):
        sink = LogSink(root=str(tmp_path), rotate_bytes=4096, backups=2).start()
        for i in range(500):
            sink.emit(LogRecord(evt="t", tier=TIER_APP, data={"i": i}))
        sink.stop()
        segments = [f for f in os.listdir(sink.run_dir) if f.startswith("app.ndjson")]
        assert "app.ndjson.1" in segments
        # backups=2 bounds retention: the oldest segment is discarded, not kept.
        assert len(segments) <= 3

    def test_transient_write_failure_loses_nothing(self, tmp_path):
        """A handle dying under us should cost zero records, not the one
        unlucky record that happened to hit it."""
        sink = LogSink(root=str(tmp_path)).start()
        sink.emit(LogRecord(evt="before", tier=TIER_APP))
        time.sleep(0.2)
        sink._fh.close()
        sink.emit(LogRecord(evt="after", tier=TIER_APP))
        time.sleep(0.3)
        events = [r["evt"] for r in _drain(sink)]
        assert "before" in events and "after" in events
        assert sink.stats()["write_failures"] >= 1

    def test_emit_never_blocks_when_queue_is_full(self, tmp_path):
        """Wire logging sits in the serial read loop; a stalled disk must not
        stall acquisition."""
        from reacher.diagnostics import sink as sinkmod

        sink = LogSink(root=str(tmp_path))  # never started -> nothing drains
        os.makedirs(sink.run_dir, exist_ok=True)
        for _ in range(sinkmod.QUEUE_MAX + 50):
            sink.emit(LogRecord(evt="flood", tier=TIER_APP))
        assert sink.stats()["dropped"] == 50

    def test_unwritable_root_does_not_raise(self, tmp_path):
        """A read-only home must not stop the server from booting."""
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory")
        sink = LogSink(root=str(blocker)).start()
        sink.emit(LogRecord(evt="t", tier=TIER_APP))
        sink.stop()
        assert sink.stats()["write_failures"] >= 1

    def test_latest_symlink_points_at_run(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        sink.stop()
        link = tmp_path / "latest"
        if link.exists():  # symlinks may be unavailable on some platforms
            assert os.path.realpath(link) == os.path.realpath(sink.run_dir)


class TestPruning:
    def test_keeps_newest_runs(self, tmp_path):
        for i in range(30):
            (tmp_path / f"run{i:02d}").mkdir()
        assert prune_runs(root=str(tmp_path), keep_runs=20, max_age_days=365) == 10
        assert len(os.listdir(tmp_path)) == 20

    def test_removes_aged_runs(self, tmp_path):
        old = tmp_path / "ancient"
        old.mkdir()
        stale = time.time() - 100 * 86400
        os.utime(old, (stale, stale))
        assert prune_runs(root=str(tmp_path), keep_runs=50, max_age_days=30) == 1

    def test_missing_root_is_not_an_error(self, tmp_path):
        assert prune_runs(root=str(tmp_path / "nope")) == 0


class TestStdlibBridge:
    """The bridge is the highest-leverage piece: it makes the ~205 pre-existing
    logger calls durable without touching a single call site."""

    def test_existing_logger_calls_are_captured(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        handler = bridge.install(sink, level=logging.DEBUG)
        try:
            logging.getLogger("reacher.api.app").info("REACHER API v%s listening", "3.3.0")
        finally:
            logging.getLogger().removeHandler(handler)
        records = _drain(sink)
        assert records[0]["msg"] == "REACHER API v3.3.0 listening"
        assert records[0]["tier"] == "api"

    def test_tier_is_inferred_from_module(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        handler = bridge.install(sink, level=logging.DEBUG)
        try:
            logging.getLogger("reacher.kernel.reacher").info("k")
            logging.getLogger("reacher.session_manager").info("s")
            logging.getLogger("uvicorn.access").info("u")
        finally:
            logging.getLogger().removeHandler(handler)
        assert [r["tier"] for r in _drain(sink)] == ["kernel", "kernel", "api"]

    def test_exc_info_is_captured(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        handler = bridge.install(sink, level=logging.DEBUG)
        try:
            try:
                raise ZeroDivisionError("boom")
            except ZeroDivisionError:
                logging.getLogger("reacher.kernel.reacher").error("died", exc_info=True)
        finally:
            logging.getLogger().removeHandler(handler)
        assert "ZeroDivisionError: boom" in _drain(sink)[0]["data"]["exc"]

    def test_extra_fields_are_promoted_not_duplicated(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        handler = bridge.install(sink, level=logging.DEBUG)
        try:
            logging.getLogger("reacher.api.routers.serial").info(
                "connected", extra={"evt": "serial.connect", "session_id": "abc", "port": "/dev/ttyUSB0"}
            )
        finally:
            logging.getLogger().removeHandler(handler)
        record = _drain(sink)[0]
        assert record["evt"] == "serial.connect"
        assert record["session_id"] == "abc"
        assert record["data"] == {"port": "/dev/ttyUSB0"}

    def test_secrets_from_extra_are_redacted(self, tmp_path):
        sink = LogSink(root=str(tmp_path)).start()
        handler = bridge.install(sink, level=logging.DEBUG)
        try:
            logging.getLogger("x").info("m", extra={"api_key": "SHOULD_NOT_APPEAR"})
        finally:
            logging.getLogger().removeHandler(handler)
        assert "SHOULD_NOT_APPEAR" not in open(sink.path, encoding="utf-8").read()

    def test_install_is_idempotent(self, tmp_path):
        """configure_logging runs from both main() and the lifespan; a second
        install must not double every line."""
        sink = LogSink(root=str(tmp_path)).start()
        bridge.install(sink)
        bridge.install(sink)
        handler = bridge.install(sink)
        try:
            logging.getLogger("reacher.api.app").info("once")
        finally:
            logging.getLogger().removeHandler(handler)
        assert len(_drain(sink)) == 1

    def test_bad_format_args_do_not_raise(self, tmp_path):
        """A broken format string must not take down the caller.  Exercised
        against the handler directly: routing through the root logger would
        instead trip pytest's own formatting handler."""
        sink = LogSink(root=str(tmp_path)).start()
        handler = bridge.SinkHandler(sink)
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "%d items", ("not-an-int",), None)
        handler.emit(record)
        records = _drain(sink)
        assert len(records) == 1
        assert "unformattable" in records[0]["msg"]


class TestConfigureLogging:
    def test_writes_meta_and_boot_record(self, tmp_path):
        sink = diagnostics.configure_logging(root=str(tmp_path), prune=False)
        try:
            meta = json.load(open(os.path.join(sink.run_dir, "meta.json"), encoding="utf-8"))
            assert meta["run_id"] == context.RUN_ID and "platform" in meta
        finally:
            diagnostics.reset_for_tests()

    def test_meta_never_contains_the_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REACHER_API_KEY", "SUPER_SECRET_VALUE")
        sink = diagnostics.configure_logging(root=str(tmp_path), prune=False)
        try:
            body = open(os.path.join(sink.run_dir, "meta.json"), encoding="utf-8").read()
            assert "SUPER_SECRET_VALUE" not in body
        finally:
            diagnostics.reset_for_tests()

    def test_is_idempotent(self, tmp_path):
        first = diagnostics.configure_logging(root=str(tmp_path), prune=False)
        try:
            assert diagnostics.configure_logging(root=str(tmp_path), prune=False) is first
        finally:
            diagnostics.reset_for_tests()

    def test_log_helper_is_a_noop_when_unconfigured(self):
        """Instrumentation must be safe to add anywhere, without an
        import-order dependency on configure_logging having run."""
        diagnostics.reset_for_tests()
        diagnostics.log("x.y", tier=TIER_APP)  # must not raise

    def test_log_helper_records_structured_fields(self, tmp_path):
        sink = diagnostics.configure_logging(root=str(tmp_path), prune=False)
        try:
            diagnostics.log("serial.tx", tier=TIER_WIRE, session_id="s1", line='{"cmd":101}')
            records = _drain(sink)
        finally:
            diagnostics.reset_for_tests()
        tx = [r for r in records if r["evt"] == "serial.tx"][0]
        assert tx["session_id"] == "s1" and tx["data"]["line"] == '{"cmd":101}'

    def test_uvicorn_config_propagates_to_root(self):
        """Frozen builds used to pass log_config=None, silencing uvicorn."""
        config = diagnostics.uvicorn_log_config()
        assert config["loggers"]["uvicorn.access"]["propagate"] is True
        assert config["loggers"]["uvicorn.access"]["handlers"] == []


# ---------------------------------------------------------------------------
# Phase 2/3: instrumentation and the ingest/export API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A TestClient whose diagnostic log is readable by the test."""
    monkeypatch.setenv("REACHER_LOG_DIR", str(tmp_path / "runs"))
    diagnostics.reset_for_tests()

    from fastapi.testclient import TestClient

    from reacher.api.app import create_app
    from reacher.api.middleware.auth import API_KEY

    with TestClient(create_app()) as client:
        client.headers.update({"Authorization": f"Bearer {API_KEY}"})
        yield client
    diagnostics.reset_for_tests()


def _records_of(sink) -> list[dict]:
    sink.flush_now()
    with open(sink.path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class TestRequestLogging:
    def test_request_is_recorded_with_status_and_duration(self, api):
        api.get("/api/sessions")
        records = _records_of(diagnostics.get_sink())
        hit = [r for r in records if r["evt"] == "http.request" and r["data"]["path"] == "/api/sessions"]
        assert hit and hit[-1]["data"]["status"] == 200
        assert isinstance(hit[-1]["data"]["duration_ms"], float)

    def test_corr_id_from_header_is_adopted(self, api):
        api.get("/api/sessions", headers={"X-Reacher-Corr-Id": "CLICK123"})
        records = _records_of(diagnostics.get_sink())
        assert any(r.get("corr_id") == "CLICK123" for r in records)

    def test_corr_id_is_minted_when_absent(self, api):
        api.get("/api/sessions")
        hit = [r for r in _records_of(diagnostics.get_sink()) if r["evt"] == "http.request"]
        assert all(r.get("corr_id") for r in hit)

    def test_auth_failure_is_recorded_as_a_warning(self, api):
        api.get("/api/sessions", headers={"Authorization": "Bearer wrong"})
        hit = [r for r in _records_of(diagnostics.get_sink()) if r["evt"] == "http.request"]
        assert any(r["data"]["status"] == 401 and r["lvl"] == "warn" for r in hit)

    def test_request_body_is_captured_and_redacted(self, api):
        api.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr", "api_key": "LEAKY"})
        sink = diagnostics.get_sink()
        posts = [
            r for r in _records_of(sink)
            if r["evt"] == "http.request" and r["data"]["method"] == "POST"
        ]
        assert posts and posts[-1]["data"]["body"]["port"] == "/dev/ttyUSB0"
        assert "LEAKY" not in open(sink.path, encoding="utf-8").read()

    def test_healthy_health_polls_are_not_logged(self, api):
        """/health is polled constantly by discovery and the monitor; logging
        every hit would bury everything else."""
        api.get("/health")
        hit = [
            r for r in _records_of(diagnostics.get_sink())
            if r["evt"] == "http.request" and r["data"]["path"] == "/health"
        ]
        assert hit == []

    def test_health_exposes_sink_counters(self, api):
        stats = api.get("/health").json()["logging"]
        assert set(stats) >= {"run_id", "dropped", "write_failures"}


class TestSessionLifecycleLogging:
    def test_state_transitions_are_recorded(self, api):
        session_id = api.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"}).json()["session_id"]
        sm = api.app.state.session_manager
        sm.set_state(session_id, "connected")
        sm.set_state(session_id, "running")
        moves = [r for r in _records_of(diagnostics.get_sink()) if r["evt"] == "session.state"]
        assert [(m["data"]["from_state"], m["data"]["to_state"]) for m in moves][-2:] == [
            ("idle", "connected"),
            ("connected", "running"),
        ]
        assert all(m["session_id"] == session_id for m in moves)

    def test_repeated_state_is_not_recorded_twice(self, api):
        session_id = api.post("/api/sessions", json={"port": "/dev/ttyUSB0", "paradigm": "fr"}).json()["session_id"]
        sm = api.app.state.session_manager
        sm.set_state(session_id, "running")
        before = len([r for r in _records_of(diagnostics.get_sink()) if r["evt"] == "session.state"])
        sm.set_state(session_id, "running")
        after = len([r for r in _records_of(diagnostics.get_sink()) if r["evt"] == "session.state"])
        assert after == before


class TestSerialWireLogging:
    def test_oversize_command_is_flagged(self, tmp_path):
        """The firmware silently truncates commands past its 128-byte buffer,
        so the backend is the only place this can be caught."""
        from reacher.kernel.reacher import _FIRMWARE_RX_BUFFER

        sink = diagnostics.configure_logging(root=str(tmp_path), prune=False)
        try:
            from unittest.mock import MagicMock

            from reacher.kernel.reacher import REACHER

            inst = REACHER.__new__(REACHER)
            inst.thread_lock = threading.Lock()
            inst.session_id = "s1"
            inst.logger = logging.getLogger("reacher.kernel.test")
            inst.ser = MagicMock(is_open=True)
            REACHER.send_serial_command(inst, {"cmd": 371, "pad": "x" * _FIRMWARE_RX_BUFFER})
            records = _records_of(sink)
        finally:
            diagnostics.reset_for_tests()

        assert any(r["evt"] == "serial.tx_oversize" and r["lvl"] == "warn" for r in records)
        assert any(r["evt"] == "serial.tx" for r in records)

    def test_normal_command_is_not_flagged(self, tmp_path):
        sink = diagnostics.configure_logging(root=str(tmp_path), prune=False)
        try:
            from unittest.mock import MagicMock

            from reacher.kernel.reacher import REACHER

            inst = REACHER.__new__(REACHER)
            inst.thread_lock = threading.Lock()
            inst.session_id = "s1"
            inst.logger = logging.getLogger("reacher.kernel.test")
            inst.ser = MagicMock(is_open=True)
            REACHER.send_serial_command(inst, {"cmd": 101})
            records = _records_of(sink)
        finally:
            diagnostics.reset_for_tests()

        assert not any(r["evt"] == "serial.tx_oversize" for r in records)
        tx = [r for r in records if r["evt"] == "serial.tx"]
        assert tx and tx[-1]["data"]["line"] == '{"cmd": 101}'
        assert tx[-1]["session_id"] == "s1"


class TestIngestEndpoint:
    def _post(self, api, records):
        return api.post("/api/logs/ingest", json={"records": records})

    def test_requires_auth(self, api):
        assert api.post("/api/logs/ingest", json={"records": []}, headers={"Authorization": ""}).status_code == 401

    def test_ui_records_land_in_the_same_stream(self, api):
        assert self._post(api, [{"evt": "ui.click", "msg": "Start", "corr_id": "C1", "session_id": "s1"}]).status_code == 200
        ui = [r for r in _records_of(diagnostics.get_sink()) if r["tier"] == "ui"]
        assert ui and ui[-1]["evt"] == "ui.click" and ui[-1]["corr_id"] == "C1"

    def test_values_are_kept_but_secrets_are_removed_server_side(self, api):
        """The browser is never trusted to have redacted anything."""
        self._post(api, [{"evt": "ui.change", "data": {"subject": "M12", "api_key": "LEAKY"}}])
        sink = diagnostics.get_sink()
        ui = [r for r in _records_of(sink) if r["tier"] == "ui"][-1]
        assert ui["data"]["subject"] == "M12"
        assert "LEAKY" not in open(sink.path, encoding="utf-8").read()

    def test_oversized_batch_is_rejected(self, api):
        assert self._post(api, [{"evt": "x"} for _ in range(600)]).status_code == 413

    def test_unknown_level_is_coerced_not_rejected(self, api):
        assert self._post(api, [{"evt": "x", "lvl": "BOGUS"}]).status_code == 200
        assert [r for r in _records_of(diagnostics.get_sink()) if r["tier"] == "ui"][-1]["lvl"] == "info"

    def test_malformed_record_is_a_422_not_a_500(self, api):
        assert api.post("/api/logs/ingest", json={"records": [{"no_evt": 1}]}).status_code == 422

    def test_client_clock_skew_is_flagged(self, api):
        """UI records carry browser time; a wrong clock must be visible rather
        than silently scrambling the timeline."""
        self._post(api, [{"evt": "ui.click", "ts": 1_000_000_000_000}])
        ui = [r for r in _records_of(diagnostics.get_sink()) if r["tier"] == "ui"][-1]
        assert "client_clock_skew_s" in ui["data"]


class TestLogRetrieval:
    def test_runs_lists_the_current_run(self, api):
        body = api.get("/api/logs/runs").json()
        assert body["current"] and any(r["current"] for r in body["runs"])

    def test_export_returns_a_zip_with_the_run_files(self, api):
        import io
        import zipfile

        response = api.get("/api/logs/export")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        names = [os.path.basename(n) for n in zipfile.ZipFile(io.BytesIO(response.content)).namelist()]
        assert "app.ndjson" in names and "meta.json" in names

    def test_export_flushes_the_active_run_first(self, api):
        """Without a flush the tail of the run being exported is still buffered
        and missing from the very download meant to capture it."""
        import io
        import zipfile

        api.get("/api/sessions", headers={"X-Reacher-Corr-Id": "MARKER42"})
        zf = zipfile.ZipFile(io.BytesIO(api.get("/api/logs/export").content))
        body = zf.read([n for n in zf.namelist() if n.endswith("app.ndjson")][0]).decode()
        assert "MARKER42" in body

    def test_path_traversal_is_rejected(self, api):
        assert api.get("/api/logs/export", params={"run": "../../etc"}).status_code == 400

    def test_unknown_run_is_404(self, api):
        assert api.get("/api/logs/export", params={"run": "no-such-run"}).status_code == 404


class TestEndToEndTrace:
    """The headline claim: one file, one corr_id, click through to the wire."""

    def _session(self, api):
        return api.post("/api/sessions", json={"port": "SIMULATOR", "paradigm": "fr"}).json()["session_id"]

    def test_serial_rx_and_tx_are_both_captured(self, api):
        """Guards the RX path specifically — it lives on a daemon thread and is
        easy to lose without noticing, since TX alone still looks plausible."""
        session_id = self._session(api)
        api.post(f"/api/serial/{session_id}/connect", json={})
        time.sleep(1.5)
        api.post(f"/api/hardware/{session_id}/command", json={"code": 371, "value": 8000})
        time.sleep(0.5)

        wire = [r for r in _records_of(diagnostics.get_sink()) if r["tier"] == "wire"]
        rx = [r for r in wire if r["evt"] == "serial.rx"]
        tx = [r for r in wire if r["evt"] == "serial.tx"]
        assert rx, "no serial.rx records — the RX wire log is not wired up"
        assert tx, "no serial.tx records"
        assert any('"cmd": 371' in r["data"]["line"] for r in tx)
        assert all(r["session_id"] == session_id for r in rx)

    def test_firmware_identification_is_recorded(self, api):
        session_id = self._session(api)
        api.post(f"/api/serial/{session_id}/connect", json={})
        time.sleep(1.5)
        ident = [r for r in _records_of(diagnostics.get_sink()) if r["evt"] == "firmware.identified"]
        assert ident and ident[-1]["data"]["firmware_version"]

    def test_one_corr_id_spans_ui_http_and_wire(self, api):
        """Acceptance criterion: a UI action can be traced to the bytes it put
        on the serial line."""
        corr = "TRACE0001"
        api.post(
            "/api/logs/ingest",
            json={"records": [{"evt": "ui.click", "msg": "Set", "corr_id": corr, "data": {"value": "8000"}}]},
        )
        session_id = api.post(
            "/api/sessions", headers={"X-Reacher-Corr-Id": corr},
            json={"port": "SIMULATOR", "paradigm": "fr"},
        ).json()["session_id"]
        api.post(f"/api/serial/{session_id}/connect", headers={"X-Reacher-Corr-Id": corr}, json={})
        time.sleep(1.5)
        api.post(
            f"/api/hardware/{session_id}/command",
            headers={"X-Reacher-Corr-Id": corr}, json={"code": 371, "value": 8000},
        )
        time.sleep(0.5)

        chain = [r for r in _records_of(diagnostics.get_sink()) if r.get("corr_id") == corr]
        tiers = {r["tier"] for r in chain}
        assert {"ui", "api", "wire"} <= tiers, f"chain only spans {sorted(tiers)}"
        assert any(r["evt"] == "serial.tx" and '"cmd": 371' in r["data"]["line"] for r in chain)
        # Ordering is by seq, not by any clock.
        assert [r["seq"] for r in chain] == sorted(r["seq"] for r in chain)


class TestHealthDisclosure:
    """/health is unauthenticated and served with wildcard CORS, so its payload
    must stay free of anything host-identifying."""

    def test_health_reports_counters_but_not_the_log_path(self, api):
        stats = api.get("/health").json()["logging"]
        assert set(stats) == {"run_id", "queued", "dropped", "write_failures"}
        assert "path" not in stats

    def test_health_body_contains_no_filesystem_path(self, api):
        body = api.get("/health").text
        assert os.path.expanduser("~") not in body
        assert "/REACHER/LOG" not in body

    def test_full_stats_remain_available_behind_auth(self, api):
        assert api.get("/api/logs/runs").json()["root"]


class TestFieldIdentityRedaction:
    """A DOM value is always logged under the generic key `value`, so the
    key-based denylist cannot see it. The field's identity must be consulted or
    a pairing code typed into a plain text input is written verbatim."""

    def test_normalised_keys_match_regardless_of_separator(self):
        for key in ("Pairing Code", "pairing_code", "pairingCode", "API Key", "api-key"):
            assert redact.is_secret_key(key), key

    def test_ordinary_field_names_still_do_not_match(self):
        for key in ("subject", "ratio", "duration", "destination", "port"):
            assert not redact.is_secret_key(key), key

    def test_value_is_redacted_when_its_label_is_secret(self):
        out = redact.redact_ui_field({"label": "Pairing Code", "value": "123-456"})
        assert out["value"] == redact.REDACTED

    def test_value_is_kept_when_its_label_is_ordinary(self):
        out = redact.redact_ui_field({"label": "Ratio", "value": "5"})
        assert out["value"] == "5"

    def test_record_without_a_value_is_untouched(self):
        payload = {"label": "Pairing Code"}
        assert redact.redact_ui_field(payload) == payload

    def test_ingest_redacts_a_secret_field_value_server_side(self, api):
        """The browser also does this, but the server must not rely on it."""
        api.post(
            "/api/logs/ingest",
            json={"records": [{"evt": "ui.change", "data": {"label": "Pairing Code", "value": "123-456"}}]},
        )
        sink = diagnostics.get_sink()
        ui = [r for r in _records_of(sink) if r["tier"] == "ui"][-1]
        assert ui["data"]["value"] == redact.REDACTED
        assert "123-456" not in open(sink.path, encoding="utf-8").read()

    def test_ingest_keeps_ordinary_field_values(self, api):
        api.post(
            "/api/logs/ingest",
            json={"records": [{"evt": "ui.change", "data": {"label": "Ratio", "value": "5"}}]},
        )
        ui = [r for r in _records_of(diagnostics.get_sink()) if r["tier"] == "ui"][-1]
        assert ui["data"]["value"] == "5"
