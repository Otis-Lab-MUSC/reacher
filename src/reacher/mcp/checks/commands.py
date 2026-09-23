"""Rules over the command registry, the firmware sketches, and device names."""

from __future__ import annotations

from ...schema import LITE_STRIPPED_CMD_PREFIXES as _LITE_STRIPPED_PREFIXES
from .registry import CheckContext, Result, Status, register

# Named source sets, so a failing result can say what it actually read. See the
# provenance rationale in registry.py.
_P_COMMANDS = ("firmware Commands.h (constexpr int)", "kernel CommandCode enum")
_P_SKETCHES = ("Cmd:: references in each firmware/*/[sketch].ino",)
_P_HANDLERS = (
    "Cmd:: references per sketch",
    "Cmd:: references in ReacherHelpers.cpp (the shared dispatcher)",
    "COMMAND_REGISTRY[*].paradigms",
)
_P_DEVICE_NAMES = (
    "firmware level-000 emit sites (reportDeviceConfig / reportDeviceLever + inline JSON)",
    "kernel _COMMAND_STATE_MAP",
)


def _check(ctx, cid):
    from .registry import _REGISTRY
    return _REGISTRY[cid]


@register("C1", "Commands.h matches CommandCode", "commands", requires=("schema", "firmware"))
def c1_command_parity(ctx: CheckContext) -> Result:
    check = _check(ctx, "C1")
    header = ctx.schema["firmware"]["commands_h"]
    enum = ctx.schema["python"]["command_code_enum"]
    missing_py = {n: v for n, v in header.items() if n not in enum}
    missing_fw = {n: v for n, v in enum.items() if n not in header}
    drifted = {n: {"firmware": v, "python": enum[n]} for n, v in header.items()
               if n in enum and enum[n] != v}
    if not (missing_py or missing_fw or drifted):
        return Result(check.id, check.title, check.severity, Status.PASS,
                      f"{len(header)} command codes agree",
                      provenance=_P_COMMANDS)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        "Commands.h and CommandCode disagree",
        evidence={"missing_from_python": missing_py, "missing_from_firmware": missing_fw,
                  "value_drift": drifted},
        fix_hint="Add the constant to whichever side lacks it; both must be edited together.",
        provenance=_P_COMMANDS,
        # "missing from firmware" reads as "delete it from Python" — but the
        # constant may simply live somewhere this parser does not look.
        suggests_removal=bool(missing_fw),
    )


@register("C2", "Every CommandCode has a registry spec", "commands", requires=("schema",))
def c2_registry_coverage(ctx: CheckContext) -> Result:
    check = _check(ctx, "C2")
    enum = ctx.schema["python"]["command_code_enum"]
    specs = {c["name"]: c for c in ctx.schema["python"]["commands"]}
    missing = sorted(set(enum) - set(specs))
    mismatched = {n: {"enum": enum[n], "spec": specs[n]["code"]}
                  for n in set(enum) & set(specs) if specs[n]["code"] != enum[n]}
    if not (missing or mismatched):
        return Result(check.id, check.title, check.severity, Status.PASS,
                      f"{len(enum)} codes have matching specs",
                      provenance=("CommandCode enum", "COMMAND_REGISTRY"))
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        "CommandCode and COMMAND_REGISTRY disagree",
        evidence={"missing_specs": missing, "code_mismatch": mismatched},
        fix_hint="Add a CommandSpec to COMMAND_REGISTRY for each missing code.",
        provenance=("CommandCode enum", "COMMAND_REGISTRY"),
    )


@register("C13", "Lite twins carry every command not stripped for the UNO", "firmware",
          requires=("schema", "firmware"))
def c13_lite_twin_parity(ctx: CheckContext) -> Result:
    """A _lite build is its base minus the Mega-only hardware — nothing else.

    Two-photon is most of that strip but not all of it: the external start
    trigger goes too, because the UNO has no free external-interrupt pin (INT0
    is the fixed microscope timestamp input, INT1 is the cue output).

    Compares Cmd:: reference sets rather than diffing text: the strip removes
    whole blocks whose interior lines carry no distinguishing token, so a
    line-level diff needs an ever-growing allowlist, while a set comparison is
    exact and survives reordering.
    """
    check = _check(ctx, "C13")
    sketches = {s["name"]: s for s in ctx.schema["firmware"]["sketches"]}
    missing, leaked = {}, {}
    for name, sketch in sketches.items():
        if not sketch["lite"]:
            continue
        base = sketches.get(sketch["base"])
        if base is None:
            continue
        base_refs, lite_refs = set(base["cmd_refs"]), set(sketch["cmd_refs"])
        stripped = {c for c in base_refs if c.startswith(_LITE_STRIPPED_PREFIXES)}
        if gap := sorted((base_refs - stripped) - lite_refs):
            missing[name] = gap
        if extra := sorted(c for c in lite_refs if c.startswith(_LITE_STRIPPED_PREFIXES)):
            leaked[name] = extra
    if not (missing or leaked):
        return Result(check.id, check.title, check.severity, Status.PASS,
                      f"{sum(1 for s in sketches.values() if s['lite'])} lite twins in step",
                      provenance=_P_SKETCHES)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        "a _lite twin diverged from its base for a reason other than the Mega-only strip",
        evidence={"missing_from_lite": missing, "stripped_leaked_into_lite": leaked},
        fix_hint="Mirror the change into the _lite twin; they are hand-maintained copies.",
        provenance=_P_SKETCHES,
        suggests_removal=bool(leaked),
    )


@register("C14", "Declared paradigm support has a firmware handler", "firmware",
          requires=("schema", "firmware"))
def c14_declaration_handler_parity(ctx: CheckContext) -> Result:
    """The check the existing parity test cannot perform.

    Commands.h agreeing with CommandCode says nothing about whether any sketch
    handles the command. With nine sketches and no central dispatcher, a command
    declared for a paradigm whose sketch ignores it is accepted by the UI and
    silently dropped by firmware.

    KNOWN_FIRMWARE_GAPS entries carry a "failure_mode" discriminator (see
    schema.py) that this check must honor, not just tolerate:
    - "code_rejected" (the default/legacy shape): firmware has no handler, so
      an entry naming a paradigm with no handler is exactly what's expected —
      exempt it.
    - "wrong_value_accepted": the handler DOES exist and runs (it just silently
      honors the wrong value); `paradigms` lists sketches that HANDLE it. This
      branch is only reached when no handler was found, which contradicts that
      claim — such an entry can never legitimately exempt a missing-handler
      pair, so it must fall through to a violation rather than being excused.
    """
    check = _check(ctx, "C14")
    py = ctx.schema["python"]
    sketches = {s["name"]: set(s["cmd_refs"]) for s in ctx.schema["firmware"]["sketches"]}
    common = set(ctx.schema["firmware"]["common_dispatcher_cmd_refs"])
    exempt = {**py.get("intentionally_unhandled", {}), **py.get("known_firmware_gaps", {})}

    violations = []
    for spec in py["commands"]:
        if spec["deprecated"]:
            continue
        entry = exempt.get(spec["name"])
        for paradigm in spec["paradigms"]:
            if paradigm not in sketches:
                continue
            if spec["name"] in common or spec["name"] in sketches[paradigm]:
                continue
            if (
                entry is not None
                and entry.get("failure_mode") != "wrong_value_accepted"
                and (entry["paradigms"] is None or paradigm in entry["paradigms"])
            ):
                continue
            violations.append({"command": spec["name"], "code": spec["code"], "paradigm": paradigm})
    if not violations:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      "every declared paradigm has a handler (or a recorded exemption)",
                      provenance=_P_HANDLERS)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        f"{len(violations)} declared command/paradigm pairs have no firmware handler",
        evidence={"unhandled": violations},
        fix_hint=("Add the handler to the sketch, or record it in "
                  "schema.INTENTIONALLY_UNHANDLED / KNOWN_FIRMWARE_GAPS with a justification."),
        provenance=_P_HANDLERS,
        # "no handler" reads as "remove the paradigm from the spec" — but the
        # handler may be reached by a path this Cmd:: grep does not see.
        suggests_removal=True,
    )


@register("L8", "Kernel command-state device names match the level-000 namespace",
          "device_names", requires=("schema", "firmware"))
def l8_command_state_map_names(ctx: CheckContext) -> Result:
    """Both writers into hardware_settings dedup on plain string equality.

    A _COMMAND_STATE_MAP name firmware never emits at level 000 does not collide
    with the firmware's own row — it creates a second, permanent entry for the
    same device with disjoint fields, and both reach the browser.
    """
    check = _check(ctx, "L8")
    config = set(ctx.schema["firmware"]["device_names"]["config"])
    unknown = sorted(set(ctx.schema["python"]["command_state_map_devices"]) - config)
    if not unknown:
        return Result(check.id, check.title, check.severity, Status.PASS,
                      "every command-state device name exists at level 000",
                      provenance=_P_DEVICE_NAMES)
    return Result(
        check.id, check.title, check.severity, Status.FAIL,
        f"{len(unknown)} device names would produce duplicate hardware_settings rows",
        evidence={"unknown": unknown, "level_000_namespace": sorted(config)},
        fix_hint=("Respell them the way firmware does at level 000. Note the lick circuit "
                  "is LICK at level 000 and LICK_CIRCUIT at level 007."),
        provenance=_P_DEVICE_NAMES,
    )
