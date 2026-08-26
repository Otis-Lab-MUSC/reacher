"""Tests for the SessionManager."""

import pytest
from unittest.mock import patch, Mock, MagicMock
from reacher.session_manager import SessionManager


@pytest.fixture
def sm():
    """SessionManager with mocked REACHER construction."""
    with patch("reacher.session_manager.REACHER") as MockReacher, patch("os.makedirs"):
        mock_instance = Mock()
        mock_instance.program_running = False
        mock_instance.ser = Mock()
        mock_instance.ser.is_open = False
        MockReacher.return_value = mock_instance
        yield SessionManager()


class TestCreateSession:
    def test_returns_session_id(self, sm):
        sid = sm.create_session("/dev/ttyUSB0", "fr")
        assert isinstance(sid, str)
        assert len(sid) == 32  # uuid4().hex

    def test_duplicate_port_raises(self, sm):
        sm.create_session("/dev/ttyUSB0", "fr")
        with pytest.raises(ValueError, match="already bound"):
            sm.create_session("/dev/ttyUSB0", "pr")

    def test_different_ports_ok(self, sm):
        sid1 = sm.create_session("/dev/ttyUSB0", "fr")
        sid2 = sm.create_session("/dev/ttyUSB1", "pr")
        assert sid1 != sid2


class TestGetSession:
    def test_get_existing(self, sm):
        sid = sm.create_session("/dev/ttyUSB0")
        info = sm.get_session(sid)
        assert info.port == "/dev/ttyUSB0"

    def test_get_nonexistent_raises(self, sm):
        with pytest.raises(KeyError):
            sm.get_session("nonexistent")


class TestDestroySession:
    def test_destroy_releases_port(self, sm):
        sid = sm.create_session("/dev/ttyUSB0")
        sm.destroy_session(sid)
        # Port should now be free
        sid2 = sm.create_session("/dev/ttyUSB0")
        assert sid != sid2

    def test_destroy_nonexistent_noop(self, sm):
        sm.destroy_session("nonexistent")  # Should not raise


class TestListSessions:
    def test_empty(self, sm):
        assert sm.list_sessions() == []

    def test_lists_created(self, sm):
        sm.create_session("/dev/ttyUSB0", "fr")
        sm.create_session("/dev/ttyUSB1", "vi")
        sessions = sm.list_sessions()
        assert len(sessions) == 2
        ports = {s["port"] for s in sessions}
        assert ports == {"/dev/ttyUSB0", "/dev/ttyUSB1"}


class TestSetState:
    def test_set_and_read(self, sm):
        sid = sm.create_session("/dev/ttyUSB0")
        sm.set_state(sid, "running")
        info = sm.get_session(sid)
        assert info.state == "running"


class TestDestroyAll:
    def test_clears_all(self, sm):
        sm.create_session("/dev/ttyUSB0")
        sm.create_session("/dev/ttyUSB1")
        sm.destroy_all()
        assert sm.list_sessions() == []


class TestDestroySessionCleanup:
    """F-007: destroy_session force-closes serial even if stop_program raises."""

    def test_destroy_force_closes_serial_on_exception(self, sm):
        sid = sm.create_session("/dev/ttyUSB0")
        mock_instance = sm.get_instance(sid)
        mock_instance.program_running = True
        mock_instance.ser.is_open = True
        mock_instance.stop_program.side_effect = RuntimeError("boom")

        sm.destroy_session(sid)  # Should not raise

        mock_instance.ser.close.assert_called_once()
        with pytest.raises(KeyError):
            sm.get_session(sid)


class TestThreadSafety:
    """F-008: list_sessions acquires the lock."""

    def test_lock_acquired_during_list_sessions(self, sm):
        real_lock = sm._lock
        mock_lock = MagicMock(wraps=real_lock)
        sm._lock = mock_lock

        sm.create_session("/dev/ttyUSB0")
        sm.list_sessions()

        assert mock_lock.__enter__.called
