"""Cross-repo parity between this repo's registries and labrynth's TS mirrors.

`labrynth/web/src/components/hardware/pinMeta.ts` hand-duplicates three separate
backend facts — the component keys and their SET_PIN codes, the per-component
PWM/PCINT role constraints, and the firmware default pins — and `types/index.ts`
duplicates the board id set. None of it is generated and nothing checks it.

These tests live here rather than in labrynth on purpose. The assertion needs
both sides, and a labrynth-side test could only reach this repo through the
*installed* `reacher2p` wheel — comparing the frontend against a possibly-stale
backend, which is the failure mode the whole exercise is meant to prevent. A
test here reads the labrynth checkout directly from disk. The dependency
direction of the test must be the opposite of the dependency direction of the
product.

Skipped, loudly, when no labrynth checkout is present.
"""

from pathlib import Path

import pytest

from reacher import pin_overrides
from reacher.mcp.sources import ts
from reacher.schema import PIN_SYMBOL_TO_COMPONENT, parse_pins_header
from reacher.uploader.boards import BOARD_PROFILES

REPO_ROOT = Path(__file__).resolve().parent.parent
LABRYNTH_ROOT = ts.find_labrynth_root()
FIRMWARE = REPO_ROOT / "firmware"

pytestmark = pytest.mark.skipif(
    LABRYNTH_ROOT is None,
    reason=(
        "labrynth checkout not found — frontend parity is UNVERIFIED, not passing. "
        "Clone it as a sibling of this repo to run these checks."
    ),
)


@pytest.fixture(scope="module")
def meta():
    return ts.parse_pin_meta(
        LABRYNTH_ROOT / "web" / "src" / "components" / "hardware" / "pinMeta.ts"
    )


# --- C3/C4: component identity -------------------------------------------


def test_c4_component_key_sets_match(meta):
    backend = set(pin_overrides.COMPONENT_KEYS)
    assert set(meta["component_keys"]) == backend, (
        f"pinMeta.ts COMPONENT_KEYS vs pin_overrides.COMPONENT_KEYS: "
        f"{set(meta['component_keys']) ^ backend}"
    )


def test_c4_component_union_matches_component_keys(meta):
    """The union and the ordered array are two hand-written lists of one fact."""
    assert set(meta["component_union"]) == set(meta["component_keys"])


def test_c4_component_order_is_deliberately_independent(meta):
    """Membership is shared; order is not, and that is intentional.

    The backend list follows PIN_CONSTRAINTS declaration order (grouped by
    command-code range); pinMeta.ts groups by physical panel layout with the
    levers first. This asserts they still differ, so that anyone who syncs them
    has to come here and decide whether the two orders are now one fact — rather
    than a future reader assuming from the backend comment that they already are.
    """
    assert set(meta["component_keys"]) == set(pin_overrides.COMPONENT_KEYS)
    if meta["component_keys"] == list(pin_overrides.COMPONENT_KEYS):
        pytest.fail(
            "the two COMPONENT_KEYS orders now match. If that is deliberate, make it "
            "an equality assertion and say so in pin_overrides.COMPONENT_KEYS; if it "
            "is coincidence, nothing enforces it and the next edit will break it."
        )


def test_c3_set_pin_codes_match(meta):
    backend = dict(pin_overrides.SET_PIN_CODE_FOR)
    assert meta["set_pin_code"] == backend, (
        "pinMeta.ts SET_PIN_CODE has drifted from pin_overrides.SET_PIN_CODE_FOR: "
        f"{ {k: (backend.get(k), meta['set_pin_code'].get(k)) for k in set(backend) | set(meta['set_pin_code']) if backend.get(k) != meta['set_pin_code'].get(k)} }"
    )


# --- C5: role constraints ------------------------------------------------


def _constraint_by_component() -> dict[str, pin_overrides.PinConstraint]:
    return {c.component_key: c for c in pin_overrides.PIN_CONSTRAINTS.values()}


def test_c5a_pwm_constraints_match(meta):
    backend = {k: c.requires_pwm for k, c in _constraint_by_component().items()}
    assert meta["requires_pwm"] == backend, (
        f"COMPONENT_REQUIRES_PWM vs PinConstraint.requires_pwm: "
        f"{ {k for k in backend if backend[k] != meta['requires_pwm'].get(k)} }"
    )


def test_c5b_pcint_constraints_match(meta):
    backend = {k: c.requires_pcint for k, c in _constraint_by_component().items()}
    assert meta["requires_pcint"] == backend, (
        f"COMPONENT_REQUIRES_PCINT vs PinConstraint.requires_pcint: "
        f"{ {k for k in backend if backend[k] != meta['requires_pcint'].get(k)} }"
    )


def test_c5c_no_component_requires_interrupt():
    """`requires_interrupt` is validated but never set, and has no TS mirror.

    `pin_overrides.validate_pin` enforces it, yet no PIN_CONSTRAINTS entry sets
    it and `pinMeta.ts` has no equivalent field. The day a component genuinely
    needs an interrupt-capable pin, the frontend would silently offer invalid
    ones — so this must fail loudly on that day rather than the field quietly
    doing nothing forever.
    """
    offenders = [
        c.component_key for c in pin_overrides.PIN_CONSTRAINTS.values() if c.requires_interrupt
    ]
    assert not offenders, (
        f"{offenders} now require an interrupt-capable pin, but pinMeta.ts has no "
        "COMPONENT_REQUIRES_INTERRUPT mirror. Add it there and extend this check."
    )


# --- C6: firmware defaults ------------------------------------------------


@pytest.mark.skipif(not FIRMWARE.is_dir(), reason="firmware source not present")
def test_c6_default_pins_match_firmware_pins_header(meta):
    lib_src = FIRMWARE / "libraries" / "REACHERDevices" / "src"
    pins_h = parse_pins_header(lib_src / "Pins.h")
    expected = {
        component: pins_h[symbol] for symbol, component in PIN_SYMBOL_TO_COMPONENT.items()
    }
    assert meta["default_pin"] == expected, (
        "pinMeta.ts DEFAULT_PIN has drifted from firmware Pins.h: "
        f"{ {k: (expected.get(k), meta['default_pin'].get(k)) for k in set(expected) | set(meta['default_pin']) if expected.get(k) != meta['default_pin'].get(k)} }"
    )


def test_c6_microscope_timestamp_pin_is_not_offered(meta):
    """The 2P timestamp pin is fixed at INT0 in firmware and must stay unremappable."""
    assert "microscope_ts" not in meta["component_keys"]
    assert "microscope_timestamp" not in meta["component_keys"]


# --- C7: board pin sets ---------------------------------------------------


@pytest.mark.parametrize("board", ["uno", "mega"])
def test_c7_board_pin_sets_match(board, meta):
    """Compares evaluated sets, not text.

    pinMeta.ts's `range(start, endInclusive)` has an inclusive end where Python's
    `range` is exclusive, so a textual comparison is wrong in both directions.
    """
    digital, pwm, _interrupt, pcint0 = pin_overrides.board_sets(board)
    got = meta["pin_sets"][board]
    assert got["digital"] == sorted(digital), f"{board} digital pins"
    assert got["pwm"] == sorted(pwm), f"{board} PWM pins"
    assert got["pcint0"] == sorted(pcint0), f"{board} PCINT0 pins"


def test_c7_uno_pcint0_is_wider_than_mega(meta):
    """Guards the reasoning behind the asymmetric unknown-board fallback.

    UNO PCINT0 (8-13) is wider than Mega's (10-13), which is why `board_sets`
    falls back to the *Mega* set when the board is unknown while falling back to
    UNO for everything else. If this ever inverts, that fallback becomes unsafe.
    """
    assert set(meta["pin_sets"]["mega"]["pcint0"]) < set(meta["pin_sets"]["uno"]["pcint0"])


# --- C9: board identity ---------------------------------------------------


def test_c9_board_type_union_matches_board_profiles():
    source = (LABRYNTH_ROOT / "web" / "src" / "types" / "index.ts").read_text()
    assert set(ts.parse_board_type_union(source)) == set(BOARD_PROFILES), (
        "types/index.ts BoardType has drifted from uploader/boards.py BOARD_PROFILES"
    )


def test_c9_every_board_has_a_hex_directory():
    hex_root = REPO_ROOT / "src" / "reacher" / "hex"
    missing = [b for b in BOARD_PROFILES if not (hex_root / b).is_dir()]
    assert not missing, f"boards with no hex directory: {missing}"


# --- C8: the third-board trap --------------------------------------------


def test_c8_board_lookups_are_binary_ternaries(meta):
    """Documents a known `silent` site rather than asserting it is fixed.

    `digitalPinsFor`/`pwmPinsFor`/`pcint0PinsFor` guess with `board === "mega" ?
    MEGA : UNO`. Correct at two boards; at three, the new board silently
    inherits an existing set and the UI offers pins that do not exist on it.
    Nothing in the type system catches it.

    This asserts the *current* shape, so that adding a third board to
    BOARD_PROFILES turns the latent trap into a failing test at exactly the
    moment it starts to matter.
    """
    exhaustive = meta["board_lookups_are_exhaustive"]
    if len(BOARD_PROFILES) > 2:
        assert all(exhaustive.values()), (
            f"{len(BOARD_PROFILES)} boards are now supported, but these pinMeta.ts "
            f"helpers still guess with a binary ternary: "
            f"{[fn for fn, ok in exhaustive.items() if not ok]}. "
            "Convert them to an exhaustive Record<BoardType, ...> lookup."
        )
    else:
        assert not any(exhaustive.values()), (
            "pinMeta.ts board helpers changed shape — update this check and C7"
        )
