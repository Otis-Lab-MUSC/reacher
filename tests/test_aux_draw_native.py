"""Firmware ``AuxDraw.h`` against the section-3 golden vectors, compiled as C++.

The simulator tests exercise the Python port; these exercise the C++ the board
runs. Two independent routes, each skipping loudly when its compiler is absent:

- host ``c++``: build and run a harness that prints the draws.
- the Arduino ``avr-g++``: ``-fsyntax-only`` under C++14, where ``AuxDraw`` is
  constexpr, so the golden vectors are ``static_assert``s checked by the very
  compiler that builds the hex. Nothing is linked or run.
"""

import glob
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB_SRC = REPO_ROOT / "firmware" / "libraries" / "REACHERDevices" / "src"

GOLDEN_FIRST_FIVE = [3336926330, 1697253807, 2816511904, 1955480042, 718842323]
GOLDEN_INDEPENDENT_P30 = ".SS.S......S...S.SS."
GOLDEN_BLOCK_K2_N5 = "S..S.SS...S.S...S.S."

pytestmark = pytest.mark.skipif(not (LIB_SRC / "AuxDraw.h").is_file(), reason="firmware source not present")

HOST_HARNESS = r"""
#include <stdio.h>
#include "AuxDraw.h"
int main() {
  AuxDraw d;
  d.Reset(12345, 0, 1);
  for (int i = 0; i < 5; i++) printf("%lu ", (unsigned long)d.Next());
  printf("\n");
  d.Reset(12345, 2, 5);
  for (int i = 0; i < 20; i++) putchar(d.DrawIndependent(30) ? 'S' : '.');
  printf("\n");
  d.Reset(12345, 2, 5);
  for (int i = 0; i < 20; i++) putchar(d.DrawBlock() ? 'S' : '.');
  printf("\n");
  return 0;
}
"""

STATIC_HARNESS = r"""
#include "AuxDraw.h"
constexpr bool firstFive() {
  AuxDraw d;
  d.Reset(12345, 0, 1);
  return d.Next() == %du && d.Next() == %du && d.Next() == %du && d.Next() == %du && d.Next() == %du;
}
constexpr bool matches(const char* want, bool block) {
  AuxDraw d;
  d.Reset(12345, 2, 5);
  for (int i = 0; i < 20; i++) {
    bool s = block ? d.DrawBlock() : d.DrawIndependent(30);
    if (s != (want[i] == 'S')) return false;
  }
  return true;
}
static_assert(firstFive(), "xorshift32 first five outputs");
static_assert(matches("%s", false), "independent p=30");
static_assert(matches("%s", true), "block k=2 n=5");
"""


def _avr_gxx():
    found = shutil.which("avr-g++")
    if found:
        return found
    home = Path(os.environ.get("ARDUINO_DATA_DIR", Path.home() / ".arduino15"))
    candidates = sorted(glob.glob(str(home / "packages/arduino/tools/avr-gcc/*/bin/avr-g++")))
    return candidates[-1] if candidates else None


def _static_harness(independent=GOLDEN_INDEPENDENT_P30, block=GOLDEN_BLOCK_K2_N5):
    return STATIC_HARNESS % (*GOLDEN_FIRST_FIVE, independent, block)


def test_native_host_build_matches_golden_vectors(tmp_path):
    cxx = shutil.which("c++")
    if cxx is None:
        pytest.skip("no host c++ compiler: native AuxDraw.h check runs in CI only")
    src = tmp_path / "harness.cpp"
    src.write_text(HOST_HARNESS)
    exe = tmp_path / "harness"
    subprocess.run([cxx, "-std=c++11", "-Wall", "-Werror", f"-I{LIB_SRC}", str(src), "-o", str(exe)], check=True)
    lines = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.split("\n")
    assert [int(x) for x in lines[0].split()] == GOLDEN_FIRST_FIVE
    assert lines[1] == GOLDEN_INDEPENDENT_P30
    assert lines[2] == GOLDEN_BLOCK_K2_N5


def _syntax_check(avr, tmp_path, source):
    src = tmp_path / "static_harness.cpp"
    src.write_text(source)
    return subprocess.run(
        [avr, "-std=gnu++14", "-fsyntax-only", f"-I{LIB_SRC}", str(src)], capture_output=True, text=True
    )


def test_avr_gxx_static_asserts_golden_vectors(tmp_path):
    avr = _avr_gxx()
    if avr is None:
        pytest.skip("no avr-g++ (arduino:avr core not installed): static AuxDraw.h check skipped")
    result = _syntax_check(avr, tmp_path, _static_harness())
    assert result.returncode == 0, result.stderr


def test_avr_gxx_static_assert_rejects_a_wrong_vector(tmp_path):
    """Golden-negative: the static check must be able to fail."""
    avr = _avr_gxx()
    if avr is None:
        pytest.skip("no avr-g++ (arduino:avr core not installed): static AuxDraw.h check skipped")
    result = _syntax_check(avr, tmp_path, _static_harness(independent="S" + GOLDEN_INDEPENDENT_P30[1:]))
    assert result.returncode != 0
    assert "independent p=30" in result.stderr
