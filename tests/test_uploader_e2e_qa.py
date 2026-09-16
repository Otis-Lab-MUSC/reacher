"""QA (Q6) — independent post-fix verification of avrdude conf hermeticity.

Written by the qa-engineer, deliberately without reusing anything from
``tests/test_uploader_avrdude.py``: a check that shares its helpers with the
code's own tests can only re-confirm the author's assumptions.

Two layers:

1. **argv layer** — a simulated PyInstaller bundle (``sys._MEIPASS``) with the
   binary and conf present must yield ``-C <bundled conf>`` exactly once and
   never ``-N``; with the conf removed it must log the host-conf fallback and
   fold that fact into ``last_error``.
2. **binary layer** — the *real* avrdude 7.1 extracted from the shipped
   ``labrynth-cli-3.0.1-alpha.24-linux-x64`` tarball, invoked with the argv the
   uploader actually builds, on a host whose ``/etc/avrdude.conf`` is Arch's
   8.2 file (rootless overlay mount, no sudo).  That is the user's reported
   environment; the ``-C``-stripped control reproduces the bug report verbatim
   in the same namespace, so the only variable is the fix.

Layer 2 skips everywhere except this dev box (needs the ref artifacts, Linux,
and unprivileged user namespaces).
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys

import pytest

from reacher.uploader.uploader import FirmwareUploader

# ----------------------------------------------------------------------
# Layer 1 — argv construction against a simulated bundle
# ----------------------------------------------------------------------

BOARD = "mega"
PARADIGM = "fr"


def _make_bundle(tmp_path, *, with_conf: bool, with_binary: bool = True):
    """Lay out a _MEIPASS tree and return (base, binary, conf) paths."""
    avrdude_dir = tmp_path / "avrdude"
    avrdude_dir.mkdir()
    binary = avrdude_dir / ("avrdude.exe" if sys.platform == "win32" else "avrdude")
    if with_binary:
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
    conf = avrdude_dir / "avrdude.conf"
    if with_conf:
        conf.write_text('avrdude_conf_version = "7.1";\n')
    hex_dir = tmp_path / "hex" / BOARD
    hex_dir.mkdir(parents=True)
    (hex_dir / f"{PARADIGM}.hex").write_text(":00000001FF\n")
    return str(tmp_path), str(binary), str(conf)


class _FakeStream:
    def __init__(self, lines=()):
        self._lines = list(lines)

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""

    async def read(self):
        return b""


class _FakeProc:
    def __init__(self, returncode, stderr_lines=()):
        self.returncode = returncode
        self.stdout = _FakeStream()
        self.stderr = _FakeStream(stderr_lines)

    async def wait(self):
        return self.returncode


def _capture_argv(monkeypatch, *, returncode=0, stderr_lines=()):
    """Replace the subprocess spawn and return a list that collects argv."""
    seen = []

    async def fake_exec(*cmd, **kwargs):
        seen.append(list(cmd))
        return _FakeProc(returncode, stderr_lines)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return seen


def _run_upload(uploader, port="/dev/ttyUSB0"):
    return asyncio.run(uploader.upload(PARADIGM, port, board=BOARD))


def test_bundled_conf_is_passed_as_C_exactly_once_and_never_N(tmp_path, monkeypatch):
    """Q6.1 — frozen bundle with binary + conf: -C once, -N never."""
    base, binary, conf = _make_bundle(tmp_path, with_conf=True)
    monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
    seen = _capture_argv(monkeypatch)

    uploader = FirmwareUploader()
    assert uploader.avrdude_path == binary
    assert uploader.avrdude_conf == conf
    assert _run_upload(uploader) is True

    argv = seen[0]
    assert argv.count("-C") == 1, argv
    assert argv[argv.index("-C") + 1] == conf, argv
    # -N is --noconfig (the *user* rc file, avrdude main.c:729,839-840,961) and
    # does not exist before avrdude 7.3, while the Linux bundle ships 7.1.
    assert "-N" not in argv, argv
    assert "--noconfig" not in argv, argv


def test_every_argv_shape_is_free_of_N(tmp_path, monkeypatch):
    """Q6.1 golden negative — dev, frozen-with-conf and frozen-without-conf."""
    for with_conf in (True, False):
        sub = tmp_path / f"frozen_{with_conf}"
        sub.mkdir()
        base, _, _ = _make_bundle(sub, with_conf=with_conf)
        monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
        seen = _capture_argv(monkeypatch)
        _run_upload(FirmwareUploader())
        assert "-N" not in seen[0] and "--noconfig" not in seen[0], seen[0]

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    host = tmp_path / "hostbin"
    host.mkdir()
    binary = host / "avrdude"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    hexdir = tmp_path / "devhex" / BOARD
    hexdir.mkdir(parents=True)
    (hexdir / f"{PARADIGM}.hex").write_text(":00000001FF\n")
    seen = _capture_argv(monkeypatch)
    dev = FirmwareUploader(hex_dir=str(tmp_path / "devhex"), avrdude_path=str(binary))
    assert dev.avrdude_conf is None
    _run_upload(dev)
    assert "-C" not in seen[0], seen[0]
    assert "-N" not in seen[0] and "--noconfig" not in seen[0], seen[0]


def test_missing_bundled_conf_logs_and_names_the_host_fallback(tmp_path, monkeypatch, caplog):
    """Q6.2 — B3: the packaging defect is reported, not silently tolerated."""
    base, binary, _ = _make_bundle(tmp_path, with_conf=False)
    monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
    seen = _capture_argv(
        monkeypatch,
        returncode=1,
        stderr_lines=[b"avrdude error: unable to process system wide configuration file /etc/avrdude.conf\n"],
    )

    uploader = FirmwareUploader()
    assert uploader.avrdude_path == binary
    assert uploader.avrdude_conf is None

    with caplog.at_level(logging.ERROR, logger="reacher.uploader.uploader"):
        assert _run_upload(uploader) is False

    assert "-C" not in seen[0], seen[0]

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    logged = "\n".join(errors)
    assert "no bundled avrdude.conf" in logged, logged
    assert "/etc/avrdude.conf" in logged, logged
    assert "version-mismatched host conf fails the upload" in logged, logged

    # The same fact has to reach the UI, which only ever sees last_error.
    assert "no bundled avrdude.conf" in uploader.last_error, uploader.last_error
    assert "host's system-wide config" in uploader.last_error, uploader.last_error


def test_missing_conf_error_is_logged_once_per_instance(tmp_path, monkeypatch, caplog):
    """A per-upload repeat would bury the signal in a retry loop."""
    base, _, _ = _make_bundle(tmp_path, with_conf=False)
    monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
    _capture_argv(monkeypatch)

    uploader = FirmwareUploader()
    with caplog.at_level(logging.ERROR, logger="reacher.uploader.uploader"):
        _run_upload(uploader)
        _run_upload(uploader)

    hits = [r for r in caplog.records if "no bundled avrdude.conf" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in hits]


# ----------------------------------------------------------------------
# Layer 2 — the real shipped 7.1 binary against a simulated Arch 8.2 host
# ----------------------------------------------------------------------

_FIX_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_REF_BIN = os.path.join(_FIX_ROOT, "ref", "bundle-linux", "LabrynthCLI", "_internal", "avrdude", "avrdude")
_REF_LIBS = os.path.join(_FIX_ROOT, "ref", "pkg", "ulibs", "usr", "lib", "x86_64-linux-gnu")
_UBUNTU_CONF = os.path.join(_FIX_ROOT, "ref", "pkg", "ubuntu", "etc", "avrdude.conf")
_ARCH_CONF = os.path.join(_FIX_ROOT, "ref", "pkg", "arch82", "etc", "avrdude.conf")


def _userns_available() -> bool:
    if not shutil.which("unshare"):
        return False
    probe = subprocess.run(["unshare", "-rm", "true"], capture_output=True)
    return probe.returncode == 0


_real_binary = pytest.mark.skipif(
    not (
        sys.platform.startswith("linux")
        and all(os.path.isfile(p) for p in (_REF_BIN, _UBUNTU_CONF, _ARCH_CONF))
        and os.path.isdir(_REF_LIBS)
        and _userns_available()
    ),
    reason="needs Linux, avrdude-fix/ref artifacts and unprivileged user namespaces",
)


def _real_bundle(tmp_path, *, with_conf: bool):
    """Copy the shipped 7.1 binary into a _MEIPASS layout.

    *with_conf* False reproduces the shipped linux-x64 bundle exactly —
    ``_internal/avrdude/`` holds the binary and no ``avrdude.conf`` — which
    matters because avrdude's own search finds ``<dir of executable>/
    avrdude.conf`` before ``/etc``, so a conf left beside the binary would
    make the control pass for the wrong reason.
    """
    avrdude_dir = tmp_path / "avrdude"
    avrdude_dir.mkdir(parents=True)
    binary = avrdude_dir / "avrdude"
    shutil.copy2(_REF_BIN, binary)
    binary.chmod(0o755)
    if with_conf:
        shutil.copy2(_UBUNTU_CONF, avrdude_dir / "avrdude.conf")
    hex_dir = tmp_path / "hex" / BOARD
    hex_dir.mkdir(parents=True)
    (hex_dir / f"{PARADIGM}.hex").write_text(":00000001FF\n")
    return str(tmp_path)


def _argv_the_uploader_would_run(tmp_path, monkeypatch, *, with_conf=True):
    base = _real_bundle(tmp_path, with_conf=with_conf)
    monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
    seen = _capture_argv(monkeypatch)
    uploader = FirmwareUploader()
    _run_upload(uploader, port="/dev/null")
    return seen[0]


def _run_against_arch_host(argv, tmp_path):
    """Run *argv* with /etc/avrdude.conf replaced by Arch's 8.2 conf.

    Rootless overlay over /etc inside a user namespace: nothing outside the
    namespace is touched, no sudo, and the upper layer is a tmp dir.  ``-n``
    is appended so avrdude parses the config and opens the programmer but
    never writes flash; ``/dev/null`` as the port makes the open fail, which
    is the marker that config parsing got past.
    """
    upper = tmp_path / "ovl_upper"
    work = tmp_path / "ovl_work"
    upper.mkdir(parents=True)
    work.mkdir(parents=True)
    quoted = " ".join(f"'{a}'" for a in [*argv, "-n"])
    script = (
        f"mount -t overlay overlay -o lowerdir=/etc,upperdir={upper},workdir={work} /etc && "
        f"cp '{_ARCH_CONF}' /etc/avrdude.conf && "
        f"export LD_LIBRARY_PATH='{_REF_LIBS}' && "
        f"{quoted}"
    )
    return subprocess.run(["unshare", "-rm", "sh", "-c", script], capture_output=True, text=True)


@_real_binary
def test_real_shipped_binary_clears_the_reported_error_on_an_arch_host(tmp_path, monkeypatch):
    """Q6.3 — the bug report's exact failure, and the fix clearing it.

    Control and treatment differ only by the ``-C`` the fixed uploader emits;
    same binary, same namespace, same Arch 8.2 ``/etc/avrdude.conf``.
    """
    # Control: today's shipped Linux bundle — binary, no bundled conf, no -C.
    shipped = _argv_the_uploader_would_run(tmp_path / "shipped", monkeypatch, with_conf=False)
    assert "-C" not in shipped, shipped
    before = _run_against_arch_host(shipped, tmp_path / "before")
    assert "unable to process system wide configuration file /etc/avrdude.conf" in before.stderr, before.stderr
    assert before.returncode == 1, before

    # Treatment: same binary, conf bundled beside it, argv from the fixed uploader.
    argv = _argv_the_uploader_would_run(tmp_path / "fixed", monkeypatch)
    assert argv.count("-C") == 1, argv
    assert "-N" not in argv, argv
    after = _run_against_arch_host(argv, tmp_path / "after")
    assert "unable to process system wide configuration file" not in after.stderr, after.stderr
    assert "/etc/avrdude.conf" not in after.stderr, after.stderr
    # Got past config parsing and died on the fake port, as it must.
    assert "unable to open programmer" in after.stderr, after.stderr


@_real_binary
def test_the_C_flag_itself_is_what_clears_the_error(tmp_path, monkeypatch):
    """Isolate ``-C`` as the causal agent, not avrdude's own search.

    In the shipped layout the conf sits beside the binary, where avrdude's
    second search step would find it even without ``-C``.  Putting it at
    ``_MEIPASS/avrdude/etc/avrdude.conf`` — a layout reacher searches and
    avrdude does not — makes ``-C`` the only way it can be reached, so
    stripping the flag from the very same argv must restore the failure.
    """
    base = _real_bundle(tmp_path / "b", with_conf=False)
    conf = tmp_path / "b" / "avrdude" / "etc" / "avrdude.conf"
    conf.parent.mkdir(parents=True)
    shutil.copy2(_UBUNTU_CONF, conf)
    monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
    seen = _capture_argv(monkeypatch)
    _run_upload(FirmwareUploader(), port="/dev/null")
    argv = seen[0]
    assert argv[argv.index("-C") + 1] == str(conf), argv

    with_c = _run_against_arch_host(argv, tmp_path / "with_c")
    assert "unable to process system wide configuration file" not in with_c.stderr, with_c.stderr
    assert "unable to open programmer" in with_c.stderr, with_c.stderr

    i = argv.index("-C")
    without_c = _run_against_arch_host(argv[:i] + argv[i + 2:], tmp_path / "without_c")
    assert "unable to process system wide configuration file /etc/avrdude.conf" in without_c.stderr, (
        without_c.stderr
    )


@_real_binary
def test_real_binary_reports_the_bundled_conf_as_its_system_config(tmp_path, monkeypatch):
    """The bundled conf is what avrdude actually used, not merely accepted."""
    argv = _argv_the_uploader_would_run(tmp_path, monkeypatch)
    conf = argv[argv.index("-C") + 1]
    verbose = [argv[0], "-v", *argv[1:]]
    result = _run_against_arch_host(verbose, tmp_path / "verbose")
    line = [ln for ln in result.stderr.splitlines() if "System wide configuration file" in ln]
    assert line, result.stderr
    assert conf in line[0], line[0]


def _mk(tmp_path, rel):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    return p


@pytest.mark.parametrize(
    "layout",
    ["avrdude/avrdude.conf", "avrdude/etc/avrdude.conf", "etc/avrdude.conf"],
)
def test_each_documented_bundle_layout_is_found(tmp_path, monkeypatch, layout):
    """The broadened search must actually cover all three layouts it claims."""
    base, binary, _ = _make_bundle(tmp_path, with_conf=False)
    conf = _mk(tmp_path, layout)
    monkeypatch.setattr(sys, "_MEIPASS", base, raising=False)
    seen = _capture_argv(monkeypatch)

    uploader = FirmwareUploader()
    assert uploader.avrdude_conf == str(conf)
    _run_upload(uploader)
    assert seen[0][:3] == [binary, "-C", str(conf)], seen[0]
