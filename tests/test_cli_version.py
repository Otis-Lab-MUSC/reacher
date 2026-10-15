"""``reacher --version`` / ``-V`` print the version and exit without starting a server."""

import os
import socket
import subprocess
import sys

import pytest

from reacher import __version__


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_version_and_exits(flag, tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "REACHER_NO_BROWSER": "1",
        "REACHER_PORT": str(port),
        "BROWSER": "true",
    }
    result = subprocess.run(
        [sys.executable, "-m", "reacher", flag],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{__version__}\n"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        assert s.connect_ex(("127.0.0.1", port)) != 0
