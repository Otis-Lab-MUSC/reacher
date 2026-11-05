"""Typed errors raised by the REACHER kernel."""

from __future__ import annotations


class SerialPortClosedError(Exception):
    """Raised when a command needs the serial port but the port is closed."""
