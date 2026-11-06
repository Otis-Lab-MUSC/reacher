"""Exception groups shared by the API routers.

Each tuple names the failures a router expects from one kind of call, so an
``except`` clause lists real causes and a bug in the handler itself still
surfaces instead of being reported as a failed request.
"""

from __future__ import annotations

import httpx
import serial
from fastapi import WebSocketDisconnect

from ..kernel.errors import SerialPortClosedError

# A request to another REACHER device or to the broker: connection and
# timeout failures (``httpx.HTTPError``) and a body that is not valid JSON
# (``ValueError``).
HTTP_ERRORS: tuple[type[Exception], ...] = (httpx.HTTPError, ValueError)

# A call into the kernel that talks to the board: a closed port, a serial or
# OS-level I/O failure, or a rejected argument.
KERNEL_ERRORS: tuple[type[Exception], ...] = (
    SerialPortClosedError,
    serial.SerialException,
    OSError,
    ValueError,
    RuntimeError,
)

# Reading or writing a small persisted file (pin overrides, pump target).
PERSIST_ERRORS: tuple[type[Exception], ...] = (OSError, ValueError)

# Sending a frame to a browser that may already have gone away.
WS_SEND_ERRORS: tuple[type[Exception], ...] = (WebSocketDisconnect, RuntimeError, OSError)
