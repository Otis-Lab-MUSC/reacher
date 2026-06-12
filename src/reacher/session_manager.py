"""Multi-session coordinator for REACHER.

Manages independent REACHER instances, each owning a serial port, threads,
and data stores.  Port locking prevents two sessions from claiming the same
Arduino.
"""

import uuid
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .kernel.reacher import REACHER

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Metadata for a single session."""

    session_id: str
    port: str
    paradigm: Optional[str]
    instance: REACHER
    board: Optional[str] = None
    state: str = "idle"  # idle | uploading | connected | running | paused | stopped


class SessionManager:
    """Create, track and destroy REACHER sessions.

    Attributes:
        _sessions: mapping of session_id → SessionInfo.
        _port_lock: mapping of port → session_id (prevents double-bind).
    """

    def __init__(
        self,
        event_callback: Optional[Callable[[str, str, dict], None]] = None,
    ) -> None:
        self._sessions: Dict[str, SessionInfo] = {}
        self._port_lock: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._event_callback = event_callback

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_session(self, port: str, paradigm: Optional[str] = None) -> str:
        """Create a new session bound to *port*.

        Returns the generated ``session_id``.

        Raises:
            ValueError: if the port is already in use by another session.
        """
        with self._lock:
            if port in self._port_lock:
                existing = self._port_lock[port]
                raise ValueError(
                    f"Port {port} is already bound to session {existing}"
                )

            session_id = uuid.uuid4().hex

            def _on_program_stopped():
                self.set_state(session_id, "stopped")

            # Fix: XL-003 — Wrap event callback to intercept disconnect events
            def _event_callback_wrapper(sid: str, event_type: str, data: dict):
                if event_type == "disconnect":
                    self.handle_disconnect(sid, data.get("reason", "unknown"))
                if self._event_callback:
                    self._event_callback(sid, event_type, data)

            instance = REACHER(
                session_id=session_id,
                event_callback=_event_callback_wrapper,
                on_stop=_on_program_stopped,
            )
            info = SessionInfo(
                session_id=session_id,
                port=port,
                paradigm=paradigm,
                instance=instance,
            )
            self._sessions[session_id] = info
            self._port_lock[port] = session_id

        logger.info("Created session %s on port %s", session_id, port)
        self._broadcast_state(session_id, "idle")
        return session_id

    def get_session(self, session_id: str) -> SessionInfo:
        """Get session metadata.  Raises ``KeyError`` if not found."""
        try:
            return self._sessions[session_id]
        except KeyError:
            raise KeyError(f"Session {session_id} not found")

    def get_instance(self, session_id: str) -> REACHER:
        """Convenience accessor for the REACHER instance."""
        return self.get_session(session_id).instance

    def destroy_session(self, session_id: str) -> None:
        """Tear down a session and release its port."""
        with self._lock:
            info = self._sessions.get(session_id)
            if info is None:
                return
            if info.state == "destroying":
                return  # Another thread is already tearing this down
            info.state = "destroying"

        # Clean up BEFORE removing from dict (callbacks need get_session to work)
        try:
            if info.instance.program_running:
                info.instance.stop_program()
            elif info.instance.ser.is_open:
                info.instance.close_serial()
        except Exception:
            logger.warning("Error during session %s cleanup", session_id, exc_info=True)
        finally:
            # Fix: F-007 — Ensure serial port is released even if stop_program() raises
            try:
                if info.instance.ser.is_open:
                    info.instance.ser.close()
            except Exception:
                logger.debug("Failed to force-close serial for %s", session_id, exc_info=True)

        # Now safe to remove
        with self._lock:
            self._sessions.pop(session_id, None)
            self._port_lock.pop(info.port, None)

        logger.info("Destroyed session %s", session_id)

    def list_sessions(self) -> List[dict]:
        """Return a serialisable summary of all sessions.

        Fix: F-008 — Acquire lock to prevent inconsistent snapshots during
        concurrent create/destroy operations.
        """
        with self._lock:
            return [
                {
                    "session_id": info.session_id,
                    "port": info.port,
                    "paradigm": info.paradigm,
                    "board": info.board,
                    "state": info.state,
                }
                for info in self._sessions.values()
            ]

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def set_state(self, session_id: str, state: str) -> None:
        # Fix: F-008 — Lock protects _sessions access
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            return  # Session already destroyed
        info.state = state
        self._broadcast_state(session_id, state)

    def set_paradigm(self, session_id: str, paradigm: str) -> None:
        # Fix: F-008 — Lock protects _sessions access
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            raise KeyError(f"Session {session_id} not found")
        info.paradigm = paradigm

    def set_board(self, session_id: str, board: str) -> None:
        # Fix: F-008 — Lock protects _sessions access
        with self._lock:
            info = self._sessions.get(session_id)
        if info is None:
            raise KeyError(f"Session {session_id} not found")
        info.board = board

    def destroy_all(self) -> None:
        """Tear down every active session (used during app shutdown)."""
        for sid in list(self._sessions):
            self.destroy_session(sid)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def handle_disconnect(self, session_id: str, reason: str) -> None:
        """Handle a serial disconnect event from the kernel.

        Fix: XL-003 — Transition session to 'disconnected' state and broadcast.
        """
        info = self._sessions.get(session_id)
        if info is None:
            return
        info.state = "disconnected"
        self._broadcast_state(session_id, "disconnected")
        logger.warning("Session %s disconnected: %s", session_id, reason)

    def _broadcast_state(self, session_id: str, state: str) -> None:
        if self._event_callback:
            try:
                self._event_callback(
                    session_id, "session_state", {"state": state}
                )
            except Exception:
                logger.debug("State broadcast failed", exc_info=True)
