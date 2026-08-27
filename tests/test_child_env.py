"""Child-process environment sanitizing for frozen bundles.

Regression coverage for the Labrynth v3.0.1-alpha.17 report where the frozen
app's LD_LIBRARY_PATH leaked into every child, so Arch's /bin/bash loaded the
bundle's Ubuntu readline and died on `undefined symbol: rl_print_keybinding`.
"""

from __future__ import annotations

import os
import subprocess
import sys

from reacher.child_env import clean_child_env, clean_environ


class TestCleanChildEnv:
    def test_restores_the_callers_original_path(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/home/user/lib")
        env = clean_child_env()
        assert env["LD_LIBRARY_PATH"] == "/home/user/lib"
        assert "LD_LIBRARY_PATH_ORIG" not in env

    def test_drops_the_variable_when_there_was_no_original(self, monkeypatch):
        """The common case: the user had no LD_LIBRARY_PATH, so the bootloader
        never wrote an _ORIG. Unset it rather than leaving an empty string,
        which some loaders read as the current directory."""
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        env = clean_child_env()
        assert "LD_LIBRARY_PATH" not in env

    def test_empty_original_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "")
        env = clean_child_env()
        assert "LD_LIBRARY_PATH" not in env

    def test_handles_macos_variable_too(self, monkeypatch):
        monkeypatch.setenv("DYLD_LIBRARY_PATH", "/frozen/Contents/Frameworks")
        monkeypatch.delenv("DYLD_LIBRARY_PATH_ORIG", raising=False)
        assert "DYLD_LIBRARY_PATH" not in clean_child_env()

    def test_extra_lib_dirs_are_prepended(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/home/user/lib")
        env = clean_child_env(extra_lib_dirs=["/bundle/llm"])
        assert env["LD_LIBRARY_PATH"] == f"/bundle/llm{os.pathsep}/home/user/lib"
        # The bundle's own directory must not come back with it.
        assert "_internal" not in env["LD_LIBRARY_PATH"]

    def test_extra_lib_dirs_without_an_original(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        assert clean_child_env(extra_lib_dirs=["/bundle/llm"])["LD_LIBRARY_PATH"] == "/bundle/llm"

    def test_unrelated_variables_survive(self, monkeypatch):
        monkeypatch.setenv("REACHER_PORT", "6229")
        assert clean_child_env()["REACHER_PORT"] == "6229"

    def test_does_not_mutate_the_process(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        clean_child_env()
        assert os.environ["LD_LIBRARY_PATH"] == "/frozen/bundle/_internal"


class TestCleanEnviron:
    def test_applies_and_restores(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
        with clean_environ():
            assert "LD_LIBRARY_PATH" not in os.environ
        assert os.environ["LD_LIBRARY_PATH"] == "/frozen/bundle/_internal"

    def test_restores_after_an_exception(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "/frozen/bundle/_internal")
        try:
            with clean_environ():
                raise RuntimeError("browser blew up")
        except RuntimeError:
            pass
        assert os.environ["LD_LIBRARY_PATH"] == "/frozen/bundle/_internal"


def test_a_real_child_does_not_inherit_the_bundle_path(monkeypatch, tmp_path):
    """End-to-end: the value a spawned process actually observes."""
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path / "_internal"))
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    code = "import os; print(os.environ.get('LD_LIBRARY_PATH', '<unset>'))"
    dirty = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    clean = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=clean_child_env())
    assert dirty.stdout.strip() == str(tmp_path / "_internal")
    assert clean.stdout.strip() == "<unset>"
