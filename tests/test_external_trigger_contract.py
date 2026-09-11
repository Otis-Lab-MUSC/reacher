"""HTTP contract and security audit of the external-trigger "armed" freeze.

Work Package B of a cross-repo stress test (labrynth PR #127, reacher PR #71),
orchestrated by peer session labrynth-6e. Report-first: confirmed holes are
pinned with ``xfail(strict=True)`` rather than silently accepted, so the
suite stays green while the gap stays tracked — an unexpected pass means
someone fixed it and forgot to remove the marker (same pattern the project
already uses for teardown leaks, see tests/test_external_trigger_stress.py).

Scope:
  - freeze-coverage audit: every route that mutates device or session state,
    checked against arm_trigger()'s contract ("config is applied one serial
    command per request... rejected while armed") — routers/program.py:82-91
  - /arm-trigger and /disarm-trigger state x route matrix
  - /api/proxy/{device_id}/... security (path/host escape, key leakage,
    error-code fidelity)

Findings are written up separately; each xfail below cites its finding ID.
"""

import json
import threading
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from reacher import machines
from reacher.api.app import create_app
from reacher.api.middleware.auth import API_KEY

AUTH_HEADER = {"Authorization": f"Bearer {API_KEY}"}
ALL_STATES = ["idle", "uploading", "connected", "armed", "running", "paused", "stopped"]


@pytest.fixture
def client():
    """TestClient with a mocked REACHER instance (not the real simulator).

    The freeze/state-matrix/proxy audit below is a pure HTTP-contract check —
    it only needs session-manager state transitions, not real firmware
    behavior, so it follows this repo's existing convention (see
    test_external_trigger.py::client / test_api.py::client) rather than the
    SIMULATOR-port fixture, which is reserved for genuine device-behavior
    tests (wire order, pin validation end-to-end) that reacher-ad's stress
    file already covers.
    """
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        instance = Mock()
        instance.program_running = False
        instance.ser = Mock()
        instance.ser.is_open = False
        instance.get_hardware_settings.return_value = []
        instance.get_program_running.return_value = False
        instance.get_firmware_information.return_value = {"sketch": "fr", "version": "v2.0.0"}
        instance.get_detected_paradigm.return_value = None
        instance.release_external_trigger.return_value = True
        instance.emit_failure_count = 0
        instance._firmware_ready = Mock()
        instance._firmware_ready.wait.return_value = True
        MockReacher.return_value = instance
        app = create_app()
        with TestClient(app) as c:
            c.mock_instance = instance
            yield c


def _session(client, paradigm="fr", state="connected", port="COM1"):
    resp = client.post("/api/sessions", json={"port": port}, headers=AUTH_HEADER)
    sid = resp.json()["session_id"]
    sm = client.app.state.session_manager
    sm.set_paradigm(sid, paradigm)
    sm.set_state(sid, state)
    return sid


# ---------------------------------------------------------------------------
# Task 1 — freeze coverage audit
# ---------------------------------------------------------------------------


class TestFreezeCoverageMatrix:
    """Every state-mutating route in routers/{hardware,program,serial,
    session}.py, audited against the armed freeze.

    Excluded with reasons (not device/config mutations the freeze contract
    covers): serial.py's pin-overrides/pump-target DELETE endpoints are
    keyed by *port*, not session — they edit persisted replay data, not a
    live device, so there is no ``info.state`` to gate on. file.py's data
    destination / export routes touch host-side file paths, not firmware
    config. firmware.py's upload route and serial.py's connect route get
    their own dedicated test classes below because reproducing their gaps
    needs route-specific mocking, not the generic table here.
    """

    # (method, path_template, json_body, expected_status_while_armed, note)
    ROUTES = [
        ("POST", "/api/hardware/{sid}/command", {"code": 301}, 409, "single command dispatch — hardware.py:83-90"),
        ("PUT", "/api/hardware/{sid}/pins", {"assignments": {}}, 409, "bulk pin assignment — hardware.py:199-203"),
        ("POST", "/api/program/{sid}/limit", {"type": "Time", "time_limit": 60}, 409, "limits — program.py:216-223"),
        (
            "POST",
            "/api/program/{sid}/pause",
            None,
            400,
            "state-machine gate (not armed-specific) — program.py:184-188; "
            "see report F-3 for the 400-vs-409 inconsistency",
        ),
        ("POST", "/api/program/{sid}/split", None, 400, "state-machine gate — program.py:248-252"),
        ("POST", "/api/program/{sid}/restart", None, 400, "state-machine gate — program.py:271-275"),
        ("POST", "/api/program/{sid}/arm-trigger", None, 400, "already armed — re-arm rejected, program.py:98-102"),
    ]

    @pytest.mark.parametrize("method,path_tmpl,body,expected,note", ROUTES)
    def test_route_rejects_mutation_while_armed(self, client, method, path_tmpl, body, expected, note):
        sid = _session(client, state="armed")
        path = path_tmpl.format(sid=sid)
        resp = client.request(method, path, json=body, headers=AUTH_HEADER)
        assert resp.status_code == expected, (
            f"{method} {path} ({note}): expected {expected}, got {resp.status_code}: {resp.text}"
        )

    # -- documented / verified-safe exits ---------------------------------

    EXIT_ROUTES = [
        (
            "POST",
            "/api/program/{sid}/start",
            None,
            "running",
            "'Start Now' escape hatch — disarms first, program.py:71-72",
        ),
        ("POST", "/api/program/{sid}/disarm-trigger", None, "connected", "'Cancel' escape hatch"),
        ("POST", "/api/program/{sid}/stop", None, "stopped", "releases trigger, program.py:154-159"),
        ("POST", "/api/serial/{sid}/disconnect", None, "idle", "releases before closing, serial.py:176-178"),
        ("POST", "/api/sessions/{sid}/reset", None, "connected", "reset() releases internally, reacher.py:319"),
    ]

    @pytest.mark.parametrize("method,path_tmpl,body,final_state,note", EXIT_ROUTES)
    def test_documented_exit_routes_remain_reachable_while_armed(
        self, client, method, path_tmpl, body, final_state, note
    ):
        """These are the deliberate exceptions to the freeze (docs/external-
        trigger.md "Two escape hatches"), plus reset/disconnect which the
        kernel makes safe by releasing the trigger before anything else.
        Full release-ordering assertions already live in
        test_external_trigger.py::TestArmedLifecycleExits; this only
        confirms they stay reachable and land in the right terminal state,
        completing the "enumerate every route" audit table.
        """
        sid = _session(client, state="armed")
        path = path_tmpl.format(sid=sid)
        resp = client.request(method, path, json=body, headers=AUTH_HEADER)
        assert resp.status_code == 200, f"{note}: {resp.text}"
        assert client.app.state.session_manager.get_session(sid).state == final_state

    def test_destroy_is_allowed_while_armed(self, client):
        sid = _session(client, state="armed")
        resp = client.delete(f"/api/sessions/{sid}", headers=AUTH_HEADER)
        assert resp.status_code == 200
        with pytest.raises(KeyError):
            client.app.state.session_manager.get_session(sid)


class TestFirmwareUploadWhileArmed:
    """FINDING F-1 (HIGH), fixed: upload_firmware() now rejects with 409 while
    armed, joining /command, /pins and /limit in the hard-409 family.

    A reflash is the most invasive change there is — new firmware can change
    the paradigm and its command set, which invalidates the very arm the
    operator is waiting on — so unlike connect/reset/disconnect this one is
    rejected outright rather than released-and-continued.

    The guard sits before the route closes the serial port, so a rejected
    upload leaves the armed session untouched rather than half-torn-down.
    """

    def test_upload_is_rejected_while_armed(self, client):
        sid = _session(client, state="armed")
        with (
            patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(return_value=True)),
            patch("reacher.api.routers.firmware.asyncio.sleep", new=AsyncMock(return_value=None)),
        ):
            resp = client.post(
                f"/api/firmware/upload/{sid}",
                json={"paradigm": "fr_lite", "board": "uno"},
                headers=AUTH_HEADER,
            )
        assert resp.status_code == 409

    def test_rejected_upload_leaves_the_session_armed_and_the_port_alone(self, client):
        """The 409 must not be a partial teardown: state stays "armed" and the
        route never reaches close_serial(). Pins the ordering of the guard,
        which is the part a future refactor could silently break.
        """
        sid = _session(client, state="armed")
        with (
            patch("reacher.api.routers.firmware._uploader.upload", new=AsyncMock(return_value=True)),
            patch("reacher.api.routers.firmware.asyncio.sleep", new=AsyncMock(return_value=None)),
        ):
            client.post(
                f"/api/firmware/upload/{sid}",
                json={"paradigm": "fr_lite", "board": "uno"},
                headers=AUTH_HEADER,
            )
        assert client.app.state.session_manager.get_session(sid).state == "armed"
        client.mock_instance.close_serial.assert_not_called()


class TestSerialConnectWhileArmed:
    """FINDING F-2 (HIGH), fixed in the kernel rather than in this route.

    Connect does not reject while armed — it releases first and proceeds,
    matching start's "Start Now" override, reset() and disconnect. Unlike
    config edits, connect restores already-persisted settings rather than
    mutating them, so the half-applied-config hazard the freeze exists for is
    not in play; a 409 here would make connect the odd one out for no gain.

    The release itself lives inside REACHER.open_serial(), which covers all
    three of its callers (routers/firmware.py, routers/serial.py,
    routers/session.py) instead of just this one. That is invisible from here:
    this module's ``client`` fixture builds a ``Mock()`` instance, so
    ``open_serial`` has no body and an assertion about the release would pass
    whether or not the fix existed. The real regression test runs against a
    live kernel over SimulatedSerial —
    ``tests/test_external_trigger_stress.py::TestChurnAndLifecycleExits
    ::test_reconnect_over_an_armed_open_port_releases_first``.

    What IS observable at this layer is that the route does not reject, which
    is what this test pins.
    """

    def test_connect_while_armed_is_accepted_and_lands_connected(self, client):
        sid = _session(client, state="armed")
        resp = client.post(f"/api/serial/{sid}/connect", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert client.app.state.session_manager.get_session(sid).state == "connected"


# ---------------------------------------------------------------------------
# Task 2 — /arm-trigger and /disarm-trigger state x route matrix
# ---------------------------------------------------------------------------


class TestArmTriggerStateMatrix:
    @pytest.mark.parametrize("state", ALL_STATES)
    def test_arm_only_succeeds_from_connected(self, client, state):
        sid = _session(client, state=state)
        resp = client.post(f"/api/program/{sid}/arm-trigger", headers=AUTH_HEADER)
        if state == "connected":
            assert resp.status_code == 200
            assert client.app.state.session_manager.get_session(sid).state == "armed"
        else:
            assert resp.status_code == 400, f"state={state}: {resp.text}"
            assert client.app.state.session_manager.get_session(sid).state == state


class TestDisarmTriggerStateMatrix:
    @pytest.mark.parametrize("state", ALL_STATES)
    def test_disarm_only_succeeds_from_armed(self, client, state):
        sid = _session(client, state=state)
        resp = client.post(f"/api/program/{sid}/disarm-trigger", headers=AUTH_HEADER)
        if state == "armed":
            assert resp.status_code == 200
            assert client.app.state.session_manager.get_session(sid).state == "connected"
        else:
            assert resp.status_code == 400, f"state={state}: {resp.text}"
            assert client.app.state.session_manager.get_session(sid).state == state


class TestArmDisarmValidationAndAuth:
    def test_arm_unknown_session_404(self, client):
        resp = client.post("/api/program/doesnotexist/arm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 404

    def test_disarm_unknown_session_404(self, client):
        resp = client.post("/api/program/doesnotexist/disarm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 404

    def test_arm_malformed_body_is_ignored_not_422(self, client):
        """Neither route declares a Pydantic body model — a garbage payload
        must not crash the handler; the state gate is the only real check.
        """
        sid = _session(client, state="connected")
        resp = client.post(
            f"/api/program/{sid}/arm-trigger",
            content=b"{not json",
            headers={**AUTH_HEADER, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    def test_disarm_malformed_body_is_ignored_not_422(self, client):
        sid = _session(client, state="armed")
        resp = client.post(
            f"/api/program/{sid}/disarm-trigger",
            content=b"{not json",
            headers={**AUTH_HEADER, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200

    def test_arm_missing_token_401(self, client):
        sid = _session(client, state="connected")
        resp = client.post(f"/api/program/{sid}/arm-trigger")
        assert resp.status_code == 401

    def test_arm_invalid_token_401(self, client):
        sid = _session(client, state="connected")
        resp = client.post(f"/api/program/{sid}/arm-trigger", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_disarm_missing_token_401(self, client):
        sid = _session(client, state="armed")
        resp = client.post(f"/api/program/{sid}/disarm-trigger")
        assert resp.status_code == 401

    def test_disarm_invalid_token_401(self, client):
        sid = _session(client, state="armed")
        resp = client.post(f"/api/program/{sid}/disarm-trigger", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


class TestArmConcurrency:
    def test_concurrent_arm_requests_do_not_double_arm(self, client):
        """arm_trigger() has no lock around its check-then-set. This is
        currently race-free only because the handler has zero ``await``
        points between reading info.state and writing it — once the
        coroutine is scheduled it runs to completion without yielding, so
        FastAPI's single event loop can't interleave two calls mid-handler.
        That's an accident of the current implementation, not an explicit
        guarantee (contrast session_manager.destroy_session's explicit
        threading.Lock) — see report F-4.
        """
        sid = _session(client, state="connected")
        results = []
        n = 8
        barrier = threading.Barrier(n)

        def fire():
            barrier.wait()
            r = client.post(f"/api/program/{sid}/arm-trigger", headers=AUTH_HEADER)
            results.append(r.status_code)

        threads = [threading.Thread(target=fire) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(200) == 1, results
        assert results.count(400) == n - 1, results
        assert client.app.state.session_manager.get_session(sid).state == "armed"


# ---------------------------------------------------------------------------
# Task 3 — proxy-mode security
# ---------------------------------------------------------------------------


class TestProxySecurity:
    """/api/proxy/{device_id}/... — the local server holds the remote
    machine's API key server-side; the browser must never see it, and a
    crafted rest_path must never escape the paired machine's own host.
    """

    DEVICE_ID = "peer-mega-01"
    REMOTE_URL = "http://10.0.0.50:6229"
    REMOTE_KEY = "remote-secret-do-not-leak"

    @pytest.fixture
    def paired(self, client, monkeypatch):
        # Written directly into machines._cache (not machines.upsert()) so
        # the test never touches the real ~/.reacher/machines.json.
        monkeypatch.setitem(
            machines._cache,
            self.DEVICE_ID,
            {"url": self.REMOTE_URL, "api_key": self.REMOTE_KEY, "hostname": "peer-host", "name": "Peer Rig"},
        )
        return self.DEVICE_ID

    @pytest.fixture
    def upstream(self, client):
        calls = []

        async def fake_request(method, url, headers=None, content=None, **kwargs):
            calls.append({"method": method, "url": str(url), "headers": dict(headers or {}), "content": content})
            return httpx.Response(200, content=b'{"ok":true}', headers={"content-type": "application/json"})

        client.app.state.http_client.request = fake_request
        return calls

    @pytest.mark.parametrize(
        "rest_path",
        [
            "//evil.example.com/steal",
            "http://evil.example.com/steal",
            "https://evil.example.com/steal",
            "@evil.example.com/steal",
            "..%2f..%2fapi%2fsessions",
        ],
    )
    def test_rest_path_cannot_escape_the_paired_machines_host(self, client, paired, upstream, rest_path):
        """VERIFIED SAFE: upstream_url is built by plain f-string
        concatenation (``f"{machine['url']}/{rest_path}"``), not urljoin —
        rest_path can never override the scheme+host, only extend the path
        on the paired machine's own origin. proxy.py:142.

        A literal ``../../../etc/passwd`` is deliberately not parametrized
        here: httpx's own URL builder resolves ``..`` dot-segments against
        the request URL *before* the request is ever sent (RFC 3986 §5.2),
        so that case never reaches our server at all — it would prove
        something about the test client, not about proxy.py. The
        percent-encoded traversal case above (``..%2f..%2f...``) is the
        meaningful probe: it survives client-side normalization intact and
        still lands on the paired machine's own host.
        """
        client.get(f"/api/proxy/{paired}/{rest_path}", headers=AUTH_HEADER)
        assert len(upstream) == 1
        assert upstream[0]["url"].startswith(self.REMOTE_URL), upstream[0]["url"]

    def test_remote_api_key_never_appears_in_a_successful_response(self, client, paired, upstream):
        resp = client.get(f"/api/proxy/{paired}/api/sessions", headers=AUTH_HEADER)
        assert self.REMOTE_KEY not in resp.text
        assert self.REMOTE_KEY not in str(resp.headers)

    def test_remote_api_key_never_leaks_on_connect_error(self, client, paired):
        async def raise_connect_error(*a, **k):
            raise httpx.ConnectError("boom")

        client.app.state.http_client.request = raise_connect_error
        resp = client.get(f"/api/proxy/{paired}/api/sessions", headers=AUTH_HEADER)
        assert resp.status_code == 502
        assert self.REMOTE_KEY not in resp.text

    def test_remote_api_key_never_leaks_on_timeout(self, client, paired):
        async def raise_timeout(*a, **k):
            raise httpx.TimeoutException("boom")

        client.app.state.http_client.request = raise_timeout
        resp = client.get(f"/api/proxy/{paired}/api/sessions", headers=AUTH_HEADER)
        assert resp.status_code == 504
        assert self.REMOTE_KEY not in resp.text

    def test_ws_token_endpoint_returns_the_local_key_not_the_remote_one(self, client, paired):
        resp = client.get(f"/api/proxy/{paired}/ws-token", headers=AUTH_HEADER)
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] == API_KEY
        assert body["token"] != self.REMOTE_KEY

    def test_upstream_request_carries_the_remote_api_key_not_the_local_one(self, client, paired, upstream):
        client.post(f"/api/proxy/{paired}/api/program/sid1/arm-trigger", headers=AUTH_HEADER)
        assert upstream[0]["headers"]["Authorization"] == f"Bearer {self.REMOTE_KEY}"

    def test_409_from_upstream_passes_through_intact_not_flattened_to_500(self, client, paired):
        """An operator who can't tell "frozen because armed" (409) from a
        real server error (500) will retry — and the retry can land on a
        half-applied config. Confirms proxy.py's happy-path Response(...)
        forwards upstream.status_code verbatim; only httpx.ConnectError/
        TimeoutException get remapped (proxy.py:192-195).
        """

        async def fake_409(*a, **k):
            return httpx.Response(
                409,
                content=b'{"detail":"Session is armed and waiting for an external trigger; disarm it before changing configuration"}',
                headers={"content-type": "application/json"},
            )

        client.app.state.http_client.request = fake_409
        resp = client.post(f"/api/proxy/{paired}/api/hardware/sid1/command", json={"code": 301}, headers=AUTH_HEADER)
        assert resp.status_code == 409
        assert "armed" in resp.json()["detail"]

    def test_arming_a_remote_session_end_to_end_through_the_proxy(self, client, paired, upstream):
        resp = client.post(f"/api/proxy/{paired}/api/program/sid1/arm-trigger", headers=AUTH_HEADER)
        assert resp.status_code == 200
        assert upstream[0]["method"] == "POST"
        assert upstream[0]["url"] == f"{self.REMOTE_URL}/api/program/sid1/arm-trigger"

    def test_firmware_upload_enrichment_rejects_a_crafted_paradigm_without_touching_the_filesystem(
        self, client, paired, upstream
    ):
        """VERIFIED SAFE: paradigm/board come from the proxied request body
        and feed FirmwareUploader.get_hex_path(), which validates paradigm
        against the PARADIGMS whitelist before building any path
        (uploader.py:227-230) — a ValueError is raised and caught by
        proxy.py's enrichment try/except (proxy.py:175-179), so enrichment
        is silently skipped and the original body is forwarded unmodified.
        """
        body = json.dumps({"paradigm": "../../../etc/passwd", "board": "uno"}).encode()
        client.post(
            f"/api/proxy/{paired}/api/firmware/upload/sid1",
            content=body,
            headers={**AUTH_HEADER, "Content-Type": "application/json"},
        )
        assert len(upstream) == 1
        sent = json.loads(upstream[0]["content"])
        assert "hex_data" not in sent
