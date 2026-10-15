"""The CI guard turns a missing labrynth checkout into a failure, not a skip.

Each case runs pytest in-process against a throwaway project that carries a copy
of ``tests/conftest.py``, because the outer session's conftest does not apply
inside pytester's isolated rootdir.
"""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).parent / "conftest.py"

LABRYNTH_SKIP = '@pytest.mark.skipif(True, reason="labrynth checkout not found — parity UNVERIFIED")\n'
OTHER_SKIP = '@pytest.mark.skipif(True, reason="firmware source not present")\n'


@pytest.fixture
def guard_project(pytester):
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))

    def _write(skip_decorator):
        pytester.makepyfile(
            f"""
            import pytest

            {skip_decorator}
            def test_parity():
                assert True
            """
        )

    return _write


@pytest.fixture
def ci_env(monkeypatch):
    def _set(value):
        if value is None:
            monkeypatch.delenv("CI", raising=False)
        else:
            monkeypatch.setenv("CI", value)

    return _set


def test_labrynth_skip_fails_on_ci(guard_project, ci_env, pytester):
    guard_project(LABRYNTH_SKIP)
    ci_env("true")

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")

    assert result.ret != 0
    result.assert_outcomes(errors=1, skipped=0)
    result.stdout.fnmatch_lines(["*labrynth sibling checkout is missing in CI*"])


def test_labrynth_skip_stays_skip_without_ci(guard_project, ci_env, pytester):
    guard_project(LABRYNTH_SKIP)
    ci_env(None)

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")

    assert result.ret == 0
    result.assert_outcomes(skipped=1)


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_labrynth_skip_stays_skip_when_ci_is_falsy(guard_project, ci_env, pytester, value):
    guard_project(LABRYNTH_SKIP)
    ci_env(value)

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")

    assert result.ret == 0
    result.assert_outcomes(skipped=1)


def test_other_skip_stays_skip_on_ci(guard_project, ci_env, pytester):
    guard_project(OTHER_SKIP)
    ci_env("true")

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")

    assert result.ret == 0
    result.assert_outcomes(skipped=1)
