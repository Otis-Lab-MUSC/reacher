"""Drift detection between firmware Commands.h and the Python CommandCode enum.

The backend's CommandCode enum is a manual mirror of
firmware/libraries/REACHERDevices/src/Commands.h. Now that both files live in
this repo, this test parses the header and asserts parity so additions on
either side fail CI until the other side is updated.

The header parser lives in ``reacher.schema`` so that this test, the cross-repo
consistency checks and the MCP server all read Commands.h through one
implementation — a reformat should break one parser, not three.

Skipped when the firmware source tree is absent (e.g. running the test suite
against an installed wheel, which ships only the hex artifacts).
"""

from pathlib import Path

import pytest

from reacher.kernel.commands import COMMAND_REGISTRY, CommandCode
from reacher.schema import parse_commands_header

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_H = REPO_ROOT / "firmware" / "libraries" / "REACHERDevices" / "src" / "Commands.h"

# Backend-only codes with no firmware handler. Add an entry here (with
# justification) only when a CommandCode is intentionally backend/frontend-only
# and no sketch parses it. Empty: every CommandCode currently has a firmware
# handler. (CUE_SET_PULSE_* 374/375/384/385 were implemented in the Pavlovian
# sketch — see reacher #24.)
KNOWN_BACKEND_ONLY: set[CommandCode] = set()

pytestmark = pytest.mark.skipif(
    not COMMANDS_H.is_file(),
    reason="firmware source not present (installed-wheel run)",
)


def parse_commands_h() -> dict[str, int]:
    return parse_commands_header(COMMANDS_H)


def test_header_parsed():
    # parse_commands_header raises below its own sanity floor, so reaching this
    # assertion at all already proves the parser is not silently degraded.
    codes = parse_commands_h()
    assert len(codes) > 50, f"Commands.h parse looks broken — only {len(codes)} constants found"


def test_no_duplicate_codes_in_firmware():
    codes = parse_commands_h()
    seen: dict[int, str] = {}
    for name, value in codes.items():
        assert value not in seen, f"Commands.h assigns {value} to both {seen[value]} and {name}"
        seen[value] = name


def test_every_firmware_command_exists_in_python():
    codes = parse_commands_h()
    python_names = {member.name: member.value for member in CommandCode}
    missing = {n: v for n, v in codes.items() if n not in python_names}
    assert not missing, f"Commands.h constants missing from CommandCode: {missing}"
    mismatched = {
        n: (v, python_names[n]) for n, v in codes.items() if python_names[n] != v
    }
    assert not mismatched, f"Code value drift (firmware, python): {mismatched}"


def test_every_python_command_exists_in_firmware():
    codes = parse_commands_h()
    unexpected = {
        member.name: member.value
        for member in CommandCode
        if member.name not in codes and member not in KNOWN_BACKEND_ONLY
    }
    assert not unexpected, (
        f"CommandCode members missing from Commands.h (add to firmware or to "
        f"KNOWN_BACKEND_ONLY with justification): {unexpected}"
    )


def test_registry_covers_all_codes():
    missing = [member.name for member in CommandCode if member.value not in COMMAND_REGISTRY]
    assert not missing, f"CommandCode members absent from COMMAND_REGISTRY: {missing}"
