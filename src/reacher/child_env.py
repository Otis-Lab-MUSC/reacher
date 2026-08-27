"""Sanitize the environment for child processes launched from a frozen bundle.

PyInstaller's bootloader points ``LD_LIBRARY_PATH`` at the bundle directory so
the frozen app can resolve its own shared libraries, and every child process
inherits it.  The Labrynth bundle ships Ubuntu builds of ~36 libraries that
also exist system-wide (``libreadline``, ``libstdc++``, ``libssl``,
``libX11``, ...), so on a distribution whose system libraries differ, a child
binary silently loads the bundled copy and dies on a missing symbol::

    /bin/bash: symbol lookup error: /bin/bash: undefined symbol: rl_print_keybinding

That is Arch's bash finding the bundle's readline 8.2, which predates the
symbol.  It affects anything we spawn — the browser opener, ``zenity``,
``pkexec``, ``avrdude``, the llama.cpp summarizer — on any non-Ubuntu host.

The bootloader stashes the caller's original value in ``LD_LIBRARY_PATH_ORIG``
(and ``DYLD_LIBRARY_PATH_ORIG`` on macOS).  Restore it for children, or drop
the variable entirely when the user had none — an empty string is not the same
as unset, since some loaders read "" as the current directory.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Sequence

_LIB_PATH_VARS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")


def clean_child_env(extra_lib_dirs: Sequence[str] = ()) -> dict[str, str]:
    """Return a copy of the environment safe to hand a child process.

    *extra_lib_dirs* are prepended to the restored search path — use it for a
    bundled binary that genuinely needs its own directory.  Binaries built with
    an ``$ORIGIN`` RPATH do not, and should not be given one.
    """
    env = os.environ.copy()
    for var in _LIB_PATH_VARS:
        original = env.pop(f"{var}_ORIG", None)
        if original:
            env[var] = original
        else:
            env.pop(var, None)

    if extra_lib_dirs:
        prefix = os.pathsep.join(extra_lib_dirs)
        for var in _LIB_PATH_VARS:
            current = env.get(var)
            env[var] = f"{prefix}{os.pathsep}{current}" if current else prefix
    return env


@contextlib.contextmanager
def clean_environ(extra_lib_dirs: Sequence[str] = ()) -> Iterator[None]:
    """Apply :func:`clean_child_env` to ``os.environ`` for the duration.

    For APIs that spawn children without accepting an ``env`` argument —
    ``webbrowser.open()`` being the one that matters here.  This mutates
    process-global state, so only use it at points where nothing else is
    spawning concurrently (startup, before the server accepts traffic).
    Prefer passing ``env=clean_child_env()`` wherever the call allows it.
    """
    saved = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(clean_child_env(extra_lib_dirs))
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
