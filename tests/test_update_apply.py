"""Archive extraction and the directory swap that installs an update."""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from reacher.update.apply import ApplyError, cleanup_old_install, extract_archive, swap_install


def write_tar(path: Path, members: list[tarfile.TarInfo], payloads: dict[str, bytes] | None = None) -> Path:
    """Write a tar.gz holding the given members; payloads maps member name to file bytes."""
    payloads = payloads or {}
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            data = payloads.get(member.name)
            if data is not None:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            else:
                archive.addfile(member)
    return path


def file_member(name: str, mode: int = 0o644) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.mode = mode
    return member


def dir_member(name: str) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.DIRTYPE
    member.mode = 0o755
    return member


def link_member(name: str, target: str, *, hard: bool = False) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = tarfile.LNKTYPE if hard else tarfile.SYMTYPE
    member.linkname = target
    return member


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    path = tmp_path / "staging"
    path.mkdir()
    return path


def test_a_tar_extracts_and_returns_its_one_top_level_folder(tmp_path: Path, dest: Path) -> None:
    archive = write_tar(
        tmp_path / "a.tar.gz",
        [dir_member("Labrynth"), file_member("Labrynth/Labrynth", 0o755), file_member("Labrynth/data.txt")],
        {"Labrynth/Labrynth": b"binary", "Labrynth/data.txt": b"data"},
    )

    root = extract_archive(archive, dest)

    assert root == dest / "Labrynth"
    assert (root / "data.txt").read_bytes() == b"data"


def test_a_tar_keeps_the_executable_bit(tmp_path: Path, dest: Path) -> None:
    archive = write_tar(
        tmp_path / "a.tar.gz",
        [dir_member("Labrynth"), file_member("Labrynth/Labrynth", 0o755)],
        {"Labrynth/Labrynth": b"binary"},
    )

    root = extract_archive(archive, dest)

    assert (root / "Labrynth").stat().st_mode & 0o111


def test_a_tar_keeps_a_symlink_that_stays_inside(tmp_path: Path, dest: Path) -> None:
    archive = write_tar(
        tmp_path / "a.tar.gz",
        [
            dir_member("Labrynth.app"),
            file_member("Labrynth.app/lib.dylib"),
            link_member("Labrynth.app/lib-current.dylib", "lib.dylib"),
        ],
        {"Labrynth.app/lib.dylib": b"lib"},
    )

    root = extract_archive(archive, dest)

    assert (root / "lib-current.dylib").is_symlink()
    assert (root / "lib-current.dylib").read_bytes() == b"lib"


@pytest.mark.parametrize("name", ["../evil.txt", "Labrynth/../../evil.txt", "/abs/evil.txt"])
def test_a_tar_member_that_escapes_is_refused_and_nothing_is_written(tmp_path: Path, dest: Path, name: str) -> None:
    archive = write_tar(
        tmp_path / "a.tar.gz",
        [dir_member("Labrynth"), file_member("Labrynth/ok.txt"), file_member(name)],
        {"Labrynth/ok.txt": b"ok", name: b"evil"},
    )

    with pytest.raises(ApplyError, match="unsafe"):
        extract_archive(archive, dest)

    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("target", ["../../outside", "/etc/passwd"])
def test_a_tar_symlink_that_points_outside_is_refused(tmp_path: Path, dest: Path, target: str) -> None:
    archive = write_tar(tmp_path / "a.tar.gz", [dir_member("Labrynth"), link_member("Labrynth/bad", target)])

    with pytest.raises(ApplyError, match="unsafe"):
        extract_archive(archive, dest)


def test_a_tar_hardlink_is_refused(tmp_path: Path, dest: Path) -> None:
    archive = write_tar(
        tmp_path / "a.tar.gz",
        [dir_member("Labrynth"), file_member("Labrynth/a"), link_member("Labrynth/b", "Labrynth/a", hard=True)],
        {"Labrynth/a": b"a"},
    )

    with pytest.raises(ApplyError, match="unsafe"):
        extract_archive(archive, dest)


def test_a_tar_device_or_fifo_member_is_refused(tmp_path: Path, dest: Path) -> None:
    fifo = tarfile.TarInfo("Labrynth/pipe")
    fifo.type = tarfile.FIFOTYPE
    archive = write_tar(tmp_path / "a.tar.gz", [dir_member("Labrynth"), fifo])

    with pytest.raises(ApplyError, match="unsafe"):
        extract_archive(archive, dest)


def test_an_archive_with_two_top_level_entries_is_refused(tmp_path: Path, dest: Path) -> None:
    archive = write_tar(
        tmp_path / "a.tar.gz",
        [dir_member("Labrynth"), file_member("Labrynth/a"), file_member("stray.txt")],
        {"Labrynth/a": b"a", "stray.txt": b"s"},
    )

    with pytest.raises(ApplyError, match="one top-level"):
        extract_archive(archive, dest)


def test_an_empty_archive_is_refused(tmp_path: Path, dest: Path) -> None:
    with pytest.raises(ApplyError, match="empty"):
        extract_archive(write_tar(tmp_path / "a.tar.gz", []), dest)


def test_a_zip_extracts_and_returns_its_one_top_level_folder(tmp_path: Path, dest: Path) -> None:
    archive = write_zip(tmp_path / "a.zip", {"Labrynth/Labrynth.exe": b"exe", "Labrynth/data/x.bin": b"x"})

    root = extract_archive(archive, dest)

    assert root == dest / "Labrynth"
    assert (root / "data" / "x.bin").read_bytes() == b"x"


@pytest.mark.parametrize(
    "name", ["../evil.txt", "Labrynth/../../evil.txt", "/abs/evil.txt", "C:\\evil.txt", "..\\evil.txt"]
)
def test_a_zip_entry_that_escapes_is_refused_and_nothing_is_written(tmp_path: Path, dest: Path, name: str) -> None:
    archive = write_zip(tmp_path / "a.zip", {"Labrynth/ok.txt": b"ok", name: b"evil"})

    with pytest.raises(ApplyError, match="unsafe"):
        extract_archive(archive, dest)

    assert list(dest.iterdir()) == []
    assert not (tmp_path / "evil.txt").exists()


def test_a_corrupt_archive_is_an_apply_error_not_a_crash(tmp_path: Path, dest: Path) -> None:
    for name in ("bad.tar.gz", "bad.zip"):
        (tmp_path / name).write_bytes(b"this is not an archive")
        with pytest.raises(ApplyError, match="could not read"):
            extract_archive(tmp_path / name, dest)


def test_an_unknown_archive_type_is_refused(tmp_path: Path, dest: Path) -> None:
    (tmp_path / "a.rar").write_bytes(b"x")

    with pytest.raises(ApplyError, match="archive type"):
        extract_archive(tmp_path / "a.rar", dest)


def make_install(path: Path, marker: str) -> Path:
    path.mkdir()
    (path / "marker.txt").write_text(marker)
    return path


def test_swap_puts_the_new_version_in_place_and_keeps_the_old_beside_it(tmp_path: Path) -> None:
    install = make_install(tmp_path / "Labrynth", "old")
    staged = make_install(tmp_path / "staged-Labrynth", "new")

    swap_install(install, staged)

    assert (install / "marker.txt").read_text() == "new"
    assert (tmp_path / "Labrynth.old" / "marker.txt").read_text() == "old"
    assert not staged.exists()


def test_swap_restores_the_old_version_when_the_new_one_cannot_be_moved_in(tmp_path: Path) -> None:
    install = make_install(tmp_path / "Labrynth", "old")

    with pytest.raises(ApplyError, match="restored"):
        swap_install(install, tmp_path / "does-not-exist")

    assert (install / "marker.txt").read_text() == "old"
    assert not (tmp_path / "Labrynth.old").exists()


def test_swap_with_no_existing_install_changes_nothing(tmp_path: Path) -> None:
    staged = make_install(tmp_path / "staged", "new")

    with pytest.raises(ApplyError, match="install folder"):
        swap_install(tmp_path / "Labrynth", staged)

    assert (staged / "marker.txt").read_text() == "new"
    assert not (tmp_path / "Labrynth").exists()


def test_swap_replaces_a_leftover_old_folder_from_an_earlier_update(tmp_path: Path) -> None:
    install = make_install(tmp_path / "Labrynth", "current")
    make_install(tmp_path / "Labrynth.old", "ancient")
    staged = make_install(tmp_path / "staged", "new")

    swap_install(install, staged)

    assert (install / "marker.txt").read_text() == "new"
    assert (tmp_path / "Labrynth.old" / "marker.txt").read_text() == "current"


def test_cleanup_removes_the_old_folder_and_tolerates_none(tmp_path: Path) -> None:
    install = tmp_path / "Labrynth"
    cleanup_old_install(install)
    make_install(tmp_path / "Labrynth.old", "old")

    cleanup_old_install(install)

    assert not (tmp_path / "Labrynth.old").exists()
