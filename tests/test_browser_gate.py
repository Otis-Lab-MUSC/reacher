"""``_open_browser`` suppression gate — Fix: F-browser (2026-09-18).

``REACHER_NO_BROWSER`` must short-circuit both branches (incognito and not),
checked before either, and be independent of ``REACHER_INCOGNITO``.
"""

from unittest.mock import patch

from reacher.api.app import _open_browser


class TestNoBrowserGate:
    def test_no_browser_suppresses_default_branch(self, monkeypatch):
        monkeypatch.setenv("REACHER_NO_BROWSER", "1")
        monkeypatch.delenv("REACHER_INCOGNITO", raising=False)
        with patch("reacher.api.app.webbrowser.open") as mock_open:
            _open_browser("http://localhost:6229")
            mock_open.assert_not_called()

    def test_no_browser_suppresses_incognito_branch(self, monkeypatch):
        monkeypatch.setenv("REACHER_NO_BROWSER", "1")
        monkeypatch.setenv("REACHER_INCOGNITO", "1")
        with (
            patch("reacher.api.app.webbrowser.open") as mock_open,
            patch("reacher.api.app.subprocess.Popen") as mock_popen,
            patch("reacher.api.app.shutil.which", return_value="/usr/bin/chromium"),
        ):
            _open_browser("http://localhost:6229")
            mock_open.assert_not_called()
            mock_popen.assert_not_called()

    def test_default_branch_unaffected_when_gate_unset(self, monkeypatch):
        monkeypatch.delenv("REACHER_NO_BROWSER", raising=False)
        monkeypatch.delenv("REACHER_INCOGNITO", raising=False)
        with patch("reacher.api.app.webbrowser.open") as mock_open:
            _open_browser("http://localhost:6229")
            mock_open.assert_called_once_with("http://localhost:6229")

    def test_incognito_branch_unaffected_when_gate_unset(self, monkeypatch):
        monkeypatch.delenv("REACHER_NO_BROWSER", raising=False)
        monkeypatch.setenv("REACHER_INCOGNITO", "1")
        with (
            patch("reacher.api.app.webbrowser.open") as mock_open,
            patch("reacher.api.app.subprocess.Popen") as mock_popen,
            patch("reacher.api.app.shutil.which", return_value="/usr/bin/chromium"),
        ):
            _open_browser("http://localhost:6229")
            mock_popen.assert_called_once()
            mock_open.assert_not_called()
