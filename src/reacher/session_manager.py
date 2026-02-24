"""Multi-session coordinator for REACHER.

Manages independent REACHER instances, each owning a serial port, threads,
and data stores.  Port locking prevents two sessions from claiming the same
Arduino.
"""

import uuid
import logging
import threading
from dataclasses import dataclass, field
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

            instance = REACHER(
                session_id=session_id,
                event_callback=self._event_callback,
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

        # Now safe to remove
        with self._lock:
            self._sessions.pop(session_id, None)
            self._port_lock.pop(info.port, None)

        logger.info("Destroyed session %s", session_id)

    def list_sessions(self) -> List[dict]:
        """Return a serialisable summary of all sessions."""
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
        info = self.get_session(session_id)
        info.state = state
        self._broadcast_state(session_id, state)

    def set_paradigm(self, session_id: str, paradigm: str) -> None:
        info = self.get_session(session_id)
        info.paradigm = paradigm

    def set_board(self, session_id: str, board: str) -> None:
        info = self.get_session(session_id)
        info.board = board

    def destroy_all(self) -> None:
        """Tear down every active session (used during app shutdown)."""
        for sid in list(self._sessions):
            self.destroy_session(sid)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _broadcast_state(self, session_id: str, state: str) -> None:
        if self._event_callback:
            try:
                self._event_callback(
                    session_id, "session_state", {"state": state}
                )
            except Exception:
                logger.debug("State broadcast failed", exc_info=True)
