"""Rules comparing this repo's registries against labrynth's TypeScript mirrors.

``pinMeta.ts`` hand-duplicates three separate backend facts with nothing checking
it, and ``types/index.ts`` duplicates the board id set. These rules are the
cross-repo half of the check suite; they report UNAVAILABLE, never pass, when no
labrynth checkout is present.
"""

from __future__ import annotations

from .registry import CheckContext, Result, Severity, Status, compare_mappings, register


# Named source sets, so a failing result says what it actually read.
_P_COMPONENTS = (
    "pin_overrides.COMPONENT_KEYS",
    "pinMeta.ts COMPONENT_KEYS",
    "pinMeta.ts Component union",
)
_P_PIN_SETS = ("pin_overrides.board_sets()", "pinMeta.ts UNO_*/MEGA_* (range() evaluated)")
_P_BOARDS = ("uploader.BOARD_PROFILES", "pinMeta.ts digitalPinsFor/pwmPinsFor/pcint0PinsFor")
_P_BOARD_IDS = ("uploader.BOARD_PROFILES", "types/index.ts BoardType union")


def _check(cid):
    from .registry import _REGISTRY
    return _REGISTRY[cid]


def _constraints(ctx: CheckContext) -> dict[str, dict]:
    return {c["component_key"]: c for c in ctx.schema["python"]["pin_constraints"]}


@register("C3", "SET_PIN codes match between backend and pinMeta.ts", "pins",
          requires=("schema", "frontend"))
def c3_set_pin_codes(ctx: CheckContext) -> Result:
    backend = {c["component_key"]: c["code"] for c in ctx.schema["python"]["pin_constraints"]}
    return compare_mappings(
        _check("C3"), "pin_overrides.SET_PIN_CODE_FOR", backend,
        "pinMeta.ts SET_PIN_CODE", ctx.pin_meta["set_pin_code"],
        fix_hint="SET_PIN codes follow the x76 suffix convention; correct pinMeta.ts to match.",
    )


@register("C4", "Component key sets match", "pins", requires=("schema", "frontend"))
def c4_component_keys(ctx: CheckContext) -> Result:
    check = _check("C4")
    backend = set(ctx.schema["python"]["component_keys"])
    union = set(ctx.pin_meta["component_union"])
    keys = set(ctx.pin_meta["component_keys"])
    problems = {}
    if backend != keys:
        problems["backend_vs_COMPONENT_KEYS"] = sorted(backend ^ keys)
    if union != keys:
        # Two hand-written lists of one fact, inside a single file.
        problems["Component_union_vs_COMPONENT_KEYS"] = sorted(union ^ keys)
    if not problems:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      f"{len(backend)} components agree", provenance=_P_COMPONENTS)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        "component key sets disagree",
        evidence={"differences": problems, "backend": sorted(backend), "frontend": sorted(keys)},
        fix_hint=("Add the component to every list. Note the Component union is hand-written "
                  "and not derived from HardwareUiState, so editing one does not reach the other."),
        provenance=_P_COMPONENTS,
        suggests_removal=True,
    )


@register("C5a", "PWM role constraints match", "pins", requires=("schema", "frontend"))
def c5a_pwm(ctx: CheckContext) -> Result:
    backend = {k: c["requires_pwm"] for k, c in _constraints(ctx).items()}
    return compare_mappings(
        _check("C5a"), "PinConstraint.requires_pwm", backend,
        "pinMeta.ts COMPONENT_REQUIRES_PWM", ctx.pin_meta["requires_pwm"],
        fix_hint="A component driven by analogWrite needs a PWM-capable pin on both sides.",
    )


@register("C5b", "PCINT role constraints match", "pins", requires=("schema", "frontend"))
def c5b_pcint(ctx: CheckContext) -> Result:
    backend = {k: c["requires_pcint"] for k, c in _constraints(ctx).items()}
    return compare_mappings(
        _check("C5b"), "PinConstraint.requires_pcint", backend,
        "pinMeta.ts COMPONENT_REQUIRES_PCINT", ctx.pin_meta["requires_pcint"],
        fix_hint="PCINT0/PORTB is 10-13 on the Mega and 8-13 on the UNO.",
    )


@register("C5c", "No component requires an interrupt-capable pin", "pins",
          severity=Severity.WARNING, requires=("schema",))
def c5c_interrupt(ctx: CheckContext) -> Result:
    """`requires_interrupt` is enforced by validate_pin but has no TS mirror.

    No PIN_CONSTRAINTS entry sets it today, so the gap is harmless. The day one
    does, the frontend would silently offer non-interrupt pins — so this must
    fail loudly then, rather than the field quietly doing nothing forever.
    """
    check = _check("C5c")
    offenders = sorted(k for k, c in _constraints(ctx).items() if c["requires_interrupt"])
    if not offenders:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      "no component requires an interrupt-capable pin",
                      provenance=("pin_overrides.PIN_CONSTRAINTS",))
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        f"{len(offenders)} components now require an interrupt pin, with no frontend mirror",
        evidence={"components": offenders},
        fix_hint=("Add COMPONENT_REQUIRES_INTERRUPT to pinMeta.ts, teach validPinsFor about it, "
                  "and extend C5 to compare it."),
        provenance=("pin_overrides.PIN_CONSTRAINTS",),
    )


@register("C6", "Frontend default pins match firmware Pins.h", "pins",
          requires=("schema", "firmware", "frontend"))
def c6_default_pins(ctx: CheckContext) -> Result:
    fw = ctx.schema["firmware"]
    expected = {
        component: fw["pins_h"][symbol]
        for symbol, component in fw["pin_symbol_to_component"].items()
        if symbol in fw["pins_h"]
    }
    return compare_mappings(
        _check("C6"), "firmware Pins.h", expected,
        "pinMeta.ts DEFAULT_PIN", ctx.pin_meta["default_pin"],
        fix_hint=("These are the compile-time firmware defaults shown before any override. "
                  "The microscope timestamp pin is fixed at INT0 and has no entry by design."),
    )


@register("C7", "Board pin sets match", "pins", requires=("schema", "frontend"))
def c7_board_pin_sets(ctx: CheckContext) -> Result:
    """Compares evaluated sets, never text.

    pinMeta.ts's ``range(start, endInclusive)`` has an inclusive end where
    Python's ``range`` is exclusive. Comparing them without converting reports
    drift on every board set, forever.
    """
    check = _check("C7")
    differing = {}
    for board, backend_sets in ctx.schema["python"]["pin_sets"].items():
        frontend_sets = ctx.pin_meta["pin_sets"].get(board)
        if frontend_sets is None:
            differing[board] = {"frontend": "board absent from pinMeta.ts"}
            continue
        for kind in ("digital", "pwm", "pcint0"):
            if backend_sets[kind] != frontend_sets[kind]:
                differing[f"{board}.{kind}"] = {
                    "backend_only": sorted(set(backend_sets[kind]) - set(frontend_sets[kind])),
                    "frontend_only": sorted(set(frontend_sets[kind]) - set(backend_sets[kind])),
                }
    if not differing:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      f"{len(ctx.schema['python']['pin_sets'])} boards' pin sets agree",
                      provenance=_P_PIN_SETS)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        f"{len(differing)} board pin sets differ",
        evidence={"differing": differing},
        fix_hint="Remember pinMeta.ts range() includes its end; Python's range() does not.",
        provenance=_P_PIN_SETS,
    )


@register("C8", "Board lookups are exhaustive over BoardType", "boards",
          severity=Severity.WARNING, requires=("schema", "frontend"))
def c8_board_lookup_shape(ctx: CheckContext) -> Result:
    """A latent trap that only bites when a third board appears.

    digitalPinsFor/pwmPinsFor/pcint0PinsFor guess with ``board === "mega" ? MEGA
    : UNO``. Correct at two boards; at three, the new one silently inherits an
    existing set and the UI offers pins that do not exist on it.
    """
    check = _check("C8")
    exhaustive = ctx.pin_meta["board_lookups_are_exhaustive"]
    boards = {b["board_id"] for b in ctx.schema["python"]["board_profiles"]}
    guessing = sorted(fn for fn, ok in exhaustive.items() if not ok)
    if not guessing:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      "board pin lookups switch exhaustively", provenance=_P_BOARDS)
    if len(boards) <= 2:
        return Result(
            check.id, check.title, check.severity, Status.PASS,
            f"{len(guessing)} lookups use a binary ternary, which is correct at {len(boards)} boards",
            evidence={"latent_risk": guessing, "boards": sorted(boards)},
            fix_hint=("Converting these to Record<BoardType, ...> now would make adding a third "
                      "board a compiler error instead of a silent wrong answer."),
        )
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        f"{len(boards)} boards are supported but {len(guessing)} lookups still guess",
        evidence={"guessing": guessing, "boards": sorted(boards)},
        fix_hint="Convert to an exhaustive Record<BoardType, ...> lookup in pinMeta.ts.",
        provenance=_P_BOARDS,
    )


@register("C9", "BoardType union matches BOARD_PROFILES", "boards",
          requires=("schema", "board_types"))
def c9_board_types(ctx: CheckContext) -> Result:
    check = _check("C9")
    backend = {b["board_id"] for b in ctx.schema["python"]["board_profiles"]}
    frontend = set(ctx.board_types)
    if backend == frontend:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      f"{len(backend)} board ids agree", provenance=_P_BOARD_IDS)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        "BoardType has drifted from BOARD_PROFILES",
        evidence={"backend_only": sorted(backend - frontend), "frontend_only": sorted(frontend - backend)},
        fix_hint=("Nothing forces this: FirmwareUploadCard casts the select value with "
                  "`as BoardType`, so an unknown board flows in unchecked."),
        provenance=_P_BOARD_IDS,
        suggests_removal=bool(frontend - backend),
    )
