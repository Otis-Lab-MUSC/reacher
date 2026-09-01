"""Golden-negatives for the TypeScript parsers and the drift they feed.

The parity tests in test_frontend_parity.py all pass today, which means they
prove nothing on their own — a check that has never failed is indistinguishable
from a check that cannot fail. These build deliberately drifted pinMeta.ts
fixtures and assert the parsers report the drift rather than swallowing it.

Two failure modes matter and are tested separately:

* **Detected drift** — a value changed; the parser reads it and a comparison
  catches it.
* **Parser degradation** — a reformat defeats a regex. Here the parser must
  RAISE, because returning an empty table would read as "no drift" to every
  comparison downstream, turning a broken parser into a green build.
"""

import pytest

from reacher.mcp.sources import ts

# A minimal but structurally faithful pinMeta.ts. Kept inline rather than copied
# from labrynth so these tests keep working if that repo is absent.
FIXTURE = """
import type { BoardType } from "../../types";

export type Component =
  | "lever_rh"
  | "lever_lh"
  | "cue"
  | "cue2"
  | "pump"
  | "pump2"
  | "lick"
  | "laser"
  | "microscope_trigger"
  | "slm";

export const COMPONENT_KEYS: readonly Component[] = [
  "lever_rh", "lever_lh", "cue", "cue2", "pump",
  "pump2", "lick", "laser", "microscope_trigger", "slm",
] as const;

export const SET_PIN_CODE: Record<Component, number> = {
  lever_rh: 1076, lever_lh: 1376, cue: 376, cue2: 386, pump: 476,
  pump2: 486, lick: 576, laser: 676, microscope_trigger: 976, slm: 1176,
};

export const COMPONENT_REQUIRES_PWM: Record<Component, boolean> = {
  cue: true, cue2: true, laser: true, lever_rh: false, lever_lh: false,
  pump: false, pump2: false, lick: false, microscope_trigger: false, slm: false,
};

export const COMPONENT_REQUIRES_PCINT: Record<Component, boolean> = {
  slm: true, lever_rh: false, lever_lh: false, cue: false, cue2: false,
  pump: false, pump2: false, lick: false, laser: false, microscope_trigger: false,
};

export const DEFAULT_PIN: Record<Component, number> = {
  lever_rh: 10, lever_lh: 13, cue: 3, cue2: 7, pump: 4,
  pump2: 8, lick: 5, laser: 6, microscope_trigger: 9, slm: 11,
};

const range = (start: number, endInclusive: number): number[] =>
  Array.from({ length: endInclusive - start + 1 }, (_, i) => start + i);

export const UNO_DIGITAL: readonly number[] = range(2, 13);
export const UNO_PWM = new Set([3, 5, 6, 9, 10, 11]);
export const UNO_PCINT0: readonly number[] = range(8, 13);
export const MEGA_DIGITAL: readonly number[] = range(2, 53);
export const MEGA_PWM = new Set([...range(2, 13), 44, 45, 46]);
export const MEGA_PCINT0: readonly number[] = [10, 11, 12, 13];
"""


def test_fixture_parses_cleanly():
    """The baseline must be green, or every negative below proves nothing."""
    assert len(ts.parse_component_union(FIXTURE)) == 10
    assert ts.parse_number_record(FIXTURE, "SET_PIN_CODE")["cue"] == 376
    assert ts.parse_pin_set(FIXTURE, "UNO_PWM") == [3, 5, 6, 9, 10, 11]


def test_range_helper_end_is_treated_as_inclusive():
    """The single easiest way to get every board-set comparison wrong.

    pinMeta.ts's range(a, b) includes b; Python's range(a, b) excludes it. Read
    it the Python way and UNO_DIGITAL comes out as 2..12, drifting against the
    backend on every run.
    """
    assert ts.parse_pin_set(FIXTURE, "UNO_DIGITAL") == list(range(2, 14))
    assert ts.parse_pin_set(FIXTURE, "MEGA_DIGITAL") == list(range(2, 54))


def test_spread_range_inside_a_set_literal_is_expanded():
    """MEGA_PWM is `new Set([...range(2, 13), 44, 45, 46])` — both forms at once."""
    assert ts.parse_pin_set(FIXTURE, "MEGA_PWM") == list(range(2, 14)) + [44, 45, 46]


# --- Detected drift -------------------------------------------------------


def test_changed_set_pin_code_is_reported():
    drifted = FIXTURE.replace("cue: 376", "cue: 377")
    assert ts.parse_number_record(drifted, "SET_PIN_CODE")["cue"] == 377


def test_missing_component_is_reported():
    drifted = FIXTURE.replace('  | "slm";', ";").replace('"microscope_trigger", "slm",', '"microscope_trigger",')
    assert "slm" not in ts.parse_component_union(drifted)
    assert "slm" not in ts.parse_component_keys(drifted)


def test_changed_default_pin_is_reported():
    drifted = FIXTURE.replace("cue: 3,", "cue: 5,")
    assert ts.parse_number_record(drifted, "DEFAULT_PIN")["cue"] == 5


def test_flipped_role_constraint_is_reported():
    drifted = FIXTURE.replace(
        "export const COMPONENT_REQUIRES_PWM: Record<Component, boolean> = {\n  cue: true,",
        "export const COMPONENT_REQUIRES_PWM: Record<Component, boolean> = {\n  cue: false,",
    )
    assert ts.parse_bool_record(drifted, "COMPONENT_REQUIRES_PWM")["cue"] is False


def test_narrowed_board_range_is_reported():
    """The off-by-one a naive exclusive-end reading would introduce."""
    drifted = FIXTURE.replace("range(2, 13);", "range(2, 12);")
    assert ts.parse_pin_set(drifted, "UNO_DIGITAL") == list(range(2, 13))


# --- Parser degradation: must raise, never return empty -------------------


def test_truncated_component_union_raises():
    drifted = FIXTURE.replace(
        '  | "cue"\n  | "cue2"\n  | "pump"\n  | "pump2"\n  | "lick"\n  | "laser"\n'
        '  | "microscope_trigger"\n  | "slm";',
        '  | "cue";',
    )
    with pytest.raises(ts.TypeScriptParseError, match="Component union"):
        ts.parse_component_union(drifted)


def test_renamed_declaration_raises_rather_than_returning_empty():
    """A rename must be an error, not silently 'no entries and therefore no drift'."""
    drifted = FIXTURE.replace("SET_PIN_CODE", "SET_PIN_CODES")
    with pytest.raises(ts.TypeScriptParseError, match="not found"):
        ts.parse_number_record(drifted, "SET_PIN_CODE", ts.MIN_PIN_CODES)


def test_emptied_record_raises_below_the_floor():
    drifted = FIXTURE.replace(
        "  lever_rh: 1076, lever_lh: 1376, cue: 376, cue2: 386, pump: 476,\n"
        "  pump2: 486, lick: 576, laser: 676, microscope_trigger: 976, slm: 1176,",
        "  cue: 376,",
    )
    with pytest.raises(ts.TypeScriptParseError, match="looks broken"):
        ts.parse_number_record(drifted, "SET_PIN_CODE", ts.MIN_PIN_CODES)


def test_empty_pin_set_raises():
    drifted = FIXTURE.replace("export const UNO_PWM = new Set([3, 5, 6, 9, 10, 11]);",
                              "export const UNO_PWM = new Set([]);")
    with pytest.raises(ts.TypeScriptParseError, match="empty pin set"):
        ts.parse_pin_set(drifted, "UNO_PWM")


def test_unterminated_declaration_raises():
    with pytest.raises(ts.TypeScriptParseError, match="unterminated"):
        ts.parse_number_record("export const SET_PIN_CODE = { cue: 376,", "SET_PIN_CODE")


def test_board_type_union_floor_fires():
    with pytest.raises(ts.TypeScriptParseError):
        ts.parse_board_type_union('export type BoardType = "uno";')


# --- Workspace discovery --------------------------------------------------


def test_labrynth_discovery_requires_pin_meta(tmp_path):
    """Discovery keys on pinMeta.ts, so an unrelated sibling is not mistaken for labrynth."""
    (tmp_path / "labrynth").mkdir()
    assert ts.find_labrynth_root(tmp_path) is None

    target = tmp_path / "labrynth" / "web" / "src" / "components" / "hardware"
    target.mkdir(parents=True)
    (target / "pinMeta.ts").write_text(FIXTURE)
    assert ts.find_labrynth_root(tmp_path) == tmp_path / "labrynth"
