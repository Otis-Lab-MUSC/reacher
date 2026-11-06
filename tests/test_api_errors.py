"""The shared API exception groups cover the failures routers expect."""

from __future__ import annotations

import httpx
import serial

from reacher.api import errors
from reacher.kernel.errors import SerialPortClosedError


def test_every_group_is_a_tuple_of_exception_classes() -> None:
    """Each group can be used directly in an ``except`` clause."""
    for group in (errors.HTTP_ERRORS, errors.KERNEL_ERRORS, errors.PERSIST_ERRORS, errors.WS_SEND_ERRORS):
        assert isinstance(group, tuple)
        assert group
        assert all(isinstance(cls, type) and issubclass(cls, Exception) for cls in group)


def test_http_group_catches_timeouts_and_bad_json() -> None:
    """A timeout and a non-JSON body are both expected from a peer."""
    assert issubclass(httpx.ConnectTimeout, errors.HTTP_ERRORS)
    assert issubclass(ValueError, errors.HTTP_ERRORS)


def test_kernel_group_catches_closed_port_and_serial_failures() -> None:
    """The kernel's own closed-port error and pyserial failures are expected."""
    assert issubclass(SerialPortClosedError, errors.KERNEL_ERRORS)
    assert issubclass(serial.SerialException, errors.KERNEL_ERRORS)


def test_groups_do_not_swallow_programming_errors() -> None:
    """A bug in handler code (KeyError, AttributeError, TypeError) is not caught."""
    for group in (errors.HTTP_ERRORS, errors.KERNEL_ERRORS, errors.PERSIST_ERRORS, errors.WS_SEND_ERRORS):
        for bug in (KeyError, AttributeError, TypeError):
            assert not issubclass(bug, group)
