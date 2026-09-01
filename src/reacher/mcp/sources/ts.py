"""Parsers for labrynth's hand-maintained TypeScript mirrors.

``web/src/components/hardware/pinMeta.ts`` duplicates, by hand and with nothing
checking it, three separate backend facts: the component key set and their
SET_PIN command codes (``pin_overrides.py``), the per-component PWM/PCINT role
constraints (``pin_overrides.PIN_CONSTRAINTS``), and the firmware default pins
(``Pins.h``). ``types/index.ts`` duplicates the board id set
(``uploader/boards.py``). None of it is generated; all of it can drift silently.

These parsers are deliberately shallow — regex over source text, no TypeScript
toolchain — for the same reason ``test_command_parity.py`` regexes ``Commands.h``
rather than invoking a C++ parser: the shapes are simple, stable, and a heavier
dependency would not survive contact with a lab machine.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

#: Minimum yields. A reformat that defeats a regex must raise, not silently
#: report an empty table that a set comparison reads as "no drift".
MIN_COMPONENTS = 8
MIN_PIN_CODES = 8
MIN_BOARD_TYPES = 2


class TypeScriptParseError(RuntimeError):
    """A TS parser produced implausibly little output."""


_UNION_MEMBER_RE = re.compile(r'\|\s*"([a-z_0-9]+)"')
_STRING_ARRAY_RE = re.compile(r'"([a-z_0-9]+)"')
_NUM_ENTRY_RE = re.compile(r"(\w+)\s*:\s*(\d+)\s*,")
_BOOL_ENTRY_RE = re.compile(r"(\w+)\s*:\s*(true|false)\s*,")
_RANGE_CALL_RE = re.compile(r"range\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_SET_LITERAL_RE = re.compile(r"new\s+Set\(\s*\[(.*?)\]\s*\)", re.S)


def _block(source: str, symbol: str) -> str:
    """Return the text of the declaration named *symbol*, up to its closing brace.

    Brace-counting rather than a lazy regex, so a nested object literal inside a
    table does not truncate the block.
    """
    match = re.search(rf"\b{re.escape(symbol)}\b[^=]*=\s*", source)
    if match is None:
        raise TypeScriptParseError(f"declaration {symbol!r} not found")
    start = match.end()
    depth = 0
    for i, ch in enumerate(source[start:], start):
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise TypeScriptParseError(f"unterminated declaration {symbol!r}")


def parse_component_union(source: str) -> list[str]:
    """Parse the ``Component`` union type into its member keys.

    This union is hand-written and *not* derived from ``HardwareUiState``, which
    is why editing one does not reach the other — the two-anchor problem.
    """
    match = re.search(r"export\s+type\s+Component\s*=\s*(.*?);", source, re.S)
    if match is None:
        raise TypeScriptParseError("Component union not found")
    members = _UNION_MEMBER_RE.findall(match.group(1))
    if len(members) < MIN_COMPONENTS:
        raise TypeScriptParseError(
            f"Component union parse looks broken — found {len(members)}, "
            f"expected >= {MIN_COMPONENTS}"
        )
    return members


def parse_component_keys(source: str) -> list[str]:
    """Parse ``COMPONENT_KEYS`` — the stable UI grid order."""
    keys = _STRING_ARRAY_RE.findall(_block(source, "COMPONENT_KEYS"))
    if len(keys) < MIN_COMPONENTS:
        raise TypeScriptParseError(
            f"COMPONENT_KEYS parse looks broken — found {len(keys)}, expected >= {MIN_COMPONENTS}"
        )
    return keys


def parse_number_record(source: str, symbol: str, floor: int = 0) -> dict[str, int]:
    """Parse a ``Record<Component, number>`` literal such as SET_PIN_CODE."""
    entries = {k: int(v) for k, v in _NUM_ENTRY_RE.findall(_block(source, symbol))}
    if len(entries) < floor:
        raise TypeScriptParseError(
            f"{symbol} parse looks broken — found {len(entries)}, expected >= {floor}"
        )
    return entries


def parse_bool_record(source: str, symbol: str, floor: int = 0) -> dict[str, bool]:
    """Parse a ``Record<Component, boolean>`` literal such as COMPONENT_REQUIRES_PWM."""
    entries = {k: v == "true" for k, v in _BOOL_ENTRY_RE.findall(_block(source, symbol))}
    if len(entries) < floor:
        raise TypeScriptParseError(
            f"{symbol} parse looks broken — found {len(entries)}, expected >= {floor}"
        )
    return entries


def parse_pin_set(source: str, symbol: str) -> list[int]:
    """Parse a board pin set, evaluating the ``range()`` helper.

    ``pinMeta.ts`` defines ``range(start, endInclusive)`` — an **inclusive** end,
    where Python's ``range`` is exclusive. Comparing the two as text, or
    forgetting to convert, silently reports drift on every board set or misses
    an off-by-one in both directions. Hence: evaluate, then compare.
    """
    block = _block(source, symbol)
    pins: set[int] = set()
    for start, end in _RANGE_CALL_RE.findall(block):
        pins.update(range(int(start), int(end) + 1))  # +1: TS end is inclusive
    inner = _SET_LITERAL_RE.search(block)
    literal_scope = inner.group(1) if inner else block
    # Strip range() calls before harvesting bare numbers, or their arguments
    # would be picked up as individual pins.
    pins.update(int(n) for n in re.findall(r"\b(\d+)\b", _RANGE_CALL_RE.sub("", literal_scope)))
    if not pins:
        raise TypeScriptParseError(f"{symbol} parsed to an empty pin set")
    return sorted(pins)


def parse_board_type_union(source: str) -> list[str]:
    """Parse the ``BoardType`` union from ``types/index.ts``."""
    match = re.search(r"export\s+type\s+BoardType\s*=\s*(.*?);", source, re.S)
    if match is None:
        raise TypeScriptParseError("BoardType union not found")
    members = re.findall(r'"([a-z0-9_]+)"', match.group(1))
    if len(members) < MIN_BOARD_TYPES:
        raise TypeScriptParseError(
            f"BoardType union parse looks broken — found {len(members)}, "
            f"expected >= {MIN_BOARD_TYPES}"
        )
    return members


def parse_pin_meta(path: Path) -> dict:
    """Parse every mirrored table out of ``pinMeta.ts`` in one pass."""
    source = path.read_text()
    return {
        "component_union": parse_component_union(source),
        "component_keys": parse_component_keys(source),
        "set_pin_code": parse_number_record(source, "SET_PIN_CODE", MIN_PIN_CODES),
        "default_pin": parse_number_record(source, "DEFAULT_PIN", MIN_COMPONENTS),
        "requires_pwm": parse_bool_record(source, "COMPONENT_REQUIRES_PWM", MIN_COMPONENTS),
        "requires_pcint": parse_bool_record(source, "COMPONENT_REQUIRES_PCINT", MIN_COMPONENTS),
        "pin_sets": {
            "uno": {
                "digital": parse_pin_set(source, "UNO_DIGITAL"),
                "pwm": parse_pin_set(source, "UNO_PWM"),
                "pcint0": parse_pin_set(source, "UNO_PCINT0"),
            },
            "mega": {
                "digital": parse_pin_set(source, "MEGA_DIGITAL"),
                "pwm": parse_pin_set(source, "MEGA_PWM"),
                "pcint0": parse_pin_set(source, "MEGA_PCINT0"),
            },
        },
        "board_lookups_are_exhaustive": _board_lookups_are_exhaustive(source),
    }


def _board_lookups_are_exhaustive(source: str) -> dict[str, bool]:
    """Report whether each ``*PinsFor`` helper switches on the board or guesses.

    ``digitalPinsFor``/``pwmPinsFor``/``pcint0PinsFor`` are written as binary
    ternaries (``board === "mega" ? MEGA : UNO``). With two boards that is
    correct; with a third, the new board silently inherits one of the existing
    sets and the UI offers pins that do not exist on it. Nothing in the type
    system catches that, so it is reported as data for a rule to act on.
    """
    result = {}
    for fn in ("digitalPinsFor", "pwmPinsFor", "pcint0PinsFor"):
        match = re.search(rf"function\s+{fn}\b.*?\{{(.*?)\n\}}", source, re.S)
        body = match.group(1) if match else ""
        result[fn] = "?" not in body and bool(body)
    return result


def find_labrynth_root(start: Optional[Path] = None) -> Optional[Path]:
    """Locate a labrynth checkout as a sibling of the reacher one.

    Identified by ``pinMeta.ts`` — the file whose drift this tooling exists to
    catch — so an unrelated directory is never mistaken for it.
    """
    if start is None:
        from ...schema import find_repo_root

        reacher_root = find_repo_root()
        if reacher_root is None:
            return None
        start = reacher_root.parent
    for candidate in (start / "labrynth", start):
        if (candidate / "web" / "src" / "components" / "hardware" / "pinMeta.ts").is_file():
            return candidate
    return None
