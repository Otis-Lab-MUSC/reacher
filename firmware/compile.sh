#!/bin/bash
# Compile all REACHER firmware paradigms for every supported board.
# Requires: arduino-cli with arduino:avr board package installed.
#
# Usage:  bash compile.sh
# Output: ../src/reacher/hex/<board>/<paradigm>.hex for each (paradigm, board)
#         pair — the reacher package-data directory shipped in the wheel and
#         resolved by uploader/uploader.py::get_hex_path. Commit the refreshed
#         hex files together with the firmware source change.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HEX_DIR="$(cd "$SCRIPT_DIR/.." && pwd)/src/reacher/hex"
LIB_DIR="$SCRIPT_DIR/libraries"

mkdir -p "$HEX_DIR"

for board in mega; do
    case "$board" in
        mega) FQBN="arduino:avr:mega:cpu=atmega2560" ;;
    esac

    BOARD_DIR="$HEX_DIR/$board"
    mkdir -p "$BOARD_DIR"

    for sketch in fr pr vi omission pavlovian; do
        echo "==> Compiling $sketch for $board ($FQBN)..."
        arduino-cli compile \
            --fqbn "$FQBN" \
            --libraries "$LIB_DIR" \
            --output-dir "$BOARD_DIR" \
            "$SCRIPT_DIR/$sketch/$sketch.ino"

        # arduino-cli names the output <sketch>.ino.hex — rename to <sketch>.hex
        if [ -f "$BOARD_DIR/$sketch.ino.hex" ]; then
            mv "$BOARD_DIR/$sketch.ino.hex" "$BOARD_DIR/$sketch.hex"
        fi

        # Clean up extra build artifacts — only the .hex files belong in the
        # package-data directory (the wheel glob is reacher/hex/**/*.hex)
        rm -f "$BOARD_DIR/$sketch.ino.elf" "$BOARD_DIR/$sketch.ino.with_bootloader.hex" \
              "$BOARD_DIR/$sketch.ino.eep" "$BOARD_DIR/$sketch.ino.with_bootloader.bin" \
              "$BOARD_DIR/$sketch.ino.map"

        echo "    -> $BOARD_DIR/$sketch.hex"
    done
done

# Arduino UNO — lite builds only (RAM/flash constrained; see firmware/CLAUDE.md).
# Pavlovian has no lite variant: even stripped of Microscope + SLM it overflows
# the UNO's 32256-byte limit, so it stays MEGA-only.
UNO_FQBN="arduino:avr:uno"
UNO_DIR="$HEX_DIR/uno"
mkdir -p "$UNO_DIR"

for sketch in fr_lite pr_lite vi_lite omission_lite; do
    echo "==> Compiling $sketch for uno ($UNO_FQBN)..."
    arduino-cli compile \
        --fqbn "$UNO_FQBN" \
        --libraries "$LIB_DIR" \
        --output-dir "$UNO_DIR" \
        "$SCRIPT_DIR/$sketch/$sketch.ino"

    if [ -f "$UNO_DIR/$sketch.ino.hex" ]; then
        mv "$UNO_DIR/$sketch.ino.hex" "$UNO_DIR/$sketch.hex"
    fi

    rm -f "$UNO_DIR/$sketch.ino.elf" "$UNO_DIR/$sketch.ino.with_bootloader.hex" \
          "$UNO_DIR/$sketch.ino.eep" "$UNO_DIR/$sketch.ino.with_bootloader.bin" \
          "$UNO_DIR/$sketch.ino.map"

    echo "    -> $UNO_DIR/$sketch.hex"
done

echo ""
echo "All paradigms compiled successfully (MEGA: full; UNO: lite)."
ls -lh "$HEX_DIR"/*/*.hex
