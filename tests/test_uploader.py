"""Tests for FirmwareUploader.list_available() board-aware paradigm filtering."""

from reacher.uploader.uploader import FirmwareUploader


def _make_hex_dir(tmp_path, board_hexes: dict[str, list[str]]) -> str:
    for board, paradigms in board_hexes.items():
        board_dir = tmp_path / board
        board_dir.mkdir(parents=True, exist_ok=True)
        for paradigm in paradigms:
            (board_dir / f"{paradigm}.hex").write_bytes(b":00000001FF\n")
    return str(tmp_path)


def test_uno_only_lists_lite_paradigms(tmp_path):
    """Even if legacy full-paradigm hex files are still on disk for uno,
    only "_lite" paradigms are ever offered for that board."""
    hex_dir = _make_hex_dir(tmp_path, {
        "uno": ["fr", "pr", "vi", "omission", "pavlovian", "fr_lite"],
    })
    uploader = FirmwareUploader(hex_dir=hex_dir)
    assert uploader.list_available("uno") == ["fr_lite"]


def test_mega_lists_whatever_hex_exists(tmp_path):
    """Mega has no lite/normal restriction — it offers whatever hex is present."""
    hex_dir = _make_hex_dir(tmp_path, {
        "mega": ["fr", "pr"],
    })
    uploader = FirmwareUploader(hex_dir=hex_dir)
    assert uploader.list_available("mega") == ["fr", "pr"]


def test_uno_with_only_lite_hex_present(tmp_path):
    hex_dir = _make_hex_dir(tmp_path, {"uno": ["fr_lite"]})
    uploader = FirmwareUploader(hex_dir=hex_dir)
    assert uploader.list_available("uno") == ["fr_lite"]
