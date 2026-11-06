"""Locating the install folder and handing the swap to the launcher."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from reacher.update import handoff


def freeze(monkeypatch, executable: Path, platform_name: str) -> None:
    monkeypatch.setattr(handoff.sys, "frozen", True, raising=False)
    monkeypatch.setattr(handoff.sys, "executable", str(executable))
    monkeypatch.setattr(handoff.sys, "platform", platform_name)


def test_a_source_checkout_has_no_install_folder(monkeypatch):
    monkeypatch.delattr(handoff.sys, "frozen", raising=False)
    assert handoff.find_install_dir() is None


def test_a_frozen_linux_bundle_installs_beside_its_executable(monkeypatch, tmp_path):
    (tmp_path / "Labrynth").mkdir()
    exe = tmp_path / "Labrynth" / "Labrynth"
    exe.write_text("x")
    freeze(monkeypatch, exe, "linux")
    assert handoff.find_install_dir() == (tmp_path / "Labrynth").resolve()


def test_a_frozen_macos_app_installs_at_the_app_folder(monkeypatch, tmp_path):
    exe = tmp_path / "Labrynth.app" / "Contents" / "MacOS" / "Labrynth"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    freeze(monkeypatch, exe, "darwin")
    assert handoff.find_install_dir() == (tmp_path / "Labrynth.app").resolve()


def test_a_frozen_macos_binary_outside_an_app_has_no_install_folder(monkeypatch, tmp_path):
    exe = tmp_path / "Labrynth"
    exe.write_text("x")
    freeze(monkeypatch, exe, "darwin")
    assert handoff.find_install_dir() is None


def test_staging_is_created_beside_the_install_folder(tmp_path):
    install = tmp_path / "apps" / "Labrynth"
    install.mkdir(parents=True)
    staging = handoff.create_staging_root(install)
    assert staging.parent == install.parent
    assert staging.is_dir()
    assert staging != install


def test_staging_in_an_unwritable_parent_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        handoff.create_staging_root(tmp_path / "missing" / "Labrynth")


def test_the_command_has_the_agreed_shape():
    command = handoff.build_command(
        Path("/opt/Labrynth/Labrynth"), Path("/opt/.stage/Labrynth"), Path("/opt/Labrynth"), 4242
    )
    assert command == [
        "/opt/Labrynth/Labrynth",
        "--apply-update",
        "--staged",
        "/opt/.stage/Labrynth",
        "--install",
        "/opt/Labrynth",
        "--pid",
        "4242",
        "--relaunch",
    ]


def test_spawn_detaches_the_helper_on_posix(monkeypatch):
    popen = Mock()
    monkeypatch.setattr(handoff.subprocess, "Popen", popen)
    monkeypatch.setattr(handoff.sys, "platform", "linux")
    handoff.spawn_helper(["helper", "--apply-update"])
    args, kwargs = popen.call_args
    assert args[0] == ["helper", "--apply-update"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert "env" in kwargs


def test_spawn_detaches_the_helper_on_windows(monkeypatch):
    popen = Mock()
    monkeypatch.setattr(handoff.subprocess, "Popen", popen)
    monkeypatch.setattr(handoff.sys, "platform", "win32")
    handoff.spawn_helper(["helper"])
    kwargs = popen.call_args.kwargs
    assert kwargs["creationflags"] == handoff.DETACHED_PROCESS | handoff.CREATE_NEW_PROCESS_GROUP
    assert "start_new_session" not in kwargs


def test_the_helper_is_the_staged_copy_of_the_running_executable_on_linux(tmp_path):
    install = tmp_path / "Labrynth"
    staged = tmp_path / ".stage" / "Labrynth"
    assert handoff.staged_launcher(install / "Labrynth", install, staged) == staged / "Labrynth"


def test_the_helper_keeps_the_path_inside_a_macos_app(tmp_path):
    install = tmp_path / "Labrynth.app"
    staged = tmp_path / ".stage" / "Labrynth.app"
    executable = install / "Contents" / "MacOS" / "Labrynth"
    assert handoff.staged_launcher(executable, install, staged) == staged / "Contents" / "MacOS" / "Labrynth"


def test_an_executable_outside_the_install_folder_is_refused(tmp_path):
    with pytest.raises(ValueError):
        handoff.staged_launcher(tmp_path / "elsewhere" / "python", tmp_path / "Labrynth", tmp_path / "stage")
