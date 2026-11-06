"""Unpack a verified update archive and swap it into place."""

from __future__ import annotations

import logging
import posixpath
import re
import shutil
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

OLD_SUFFIX = ".old"
DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
FILE_MODE_MASK = 0o777
OWNER_READ_WRITE = 0o600
READ_ERRORS = (tarfile.TarError, zipfile.BadZipFile, OSError, EOFError, zlib.error)


class ApplyError(RuntimeError):
    """Raised when an update cannot be unpacked or installed."""


@dataclass(frozen=True)
class Entry:
    """One archive member that passed the safety checks."""

    parts: tuple[str, ...]
    is_dir: bool


def split_name(name: str) -> tuple[str, ...] | None:
    """Return the path parts of an archive member name, or None if the name is unsafe.

    Absolute paths, drive letters and ``..`` are unsafe. Backslashes count as
    separators so a name written on Windows cannot hide a ``..``.
    """
    normal = name.replace("\\", "/")
    if normal.startswith("/") or DRIVE_PREFIX.match(normal):
        return None
    parts = tuple(part for part in normal.split("/") if part not in ("", "."))
    if ".." in parts:
        return None
    return parts


def unsafe(name: str, reason: str) -> ApplyError:
    """Build the error raised for an archive member that must not be extracted."""
    return ApplyError(f"unsafe archive member {name!r}: {reason}")


def link_stays_inside(parts: tuple[str, ...], target: str) -> bool:
    """Return True if a symlink at parts points to a place inside the archive."""
    normal = target.replace("\\", "/")
    if normal.startswith("/") or DRIVE_PREFIX.match(normal):
        return False
    folder = posixpath.dirname("/".join(parts))
    resolved = posixpath.normpath(posixpath.join(folder, normal))
    return resolved != ".." and not resolved.startswith("../")


def check_tar_member(member: tarfile.TarInfo) -> Entry | None:
    """Return the Entry for a tar member, None for a no-op, or raise if it is unsafe."""
    parts = split_name(member.name)
    if parts is None:
        raise unsafe(member.name, "path leaves the update folder")
    if not parts:
        return None
    if member.isdir():
        return Entry(parts, is_dir=True)
    if member.issym():
        if not link_stays_inside(parts, member.linkname):
            raise unsafe(member.name, "symlink points outside the update folder")
        return Entry(parts, is_dir=False)
    if member.isfile():
        return Entry(parts, is_dir=False)
    raise unsafe(member.name, "only files, folders and inside symlinks are allowed")


def check_zip_member(info: zipfile.ZipInfo) -> Entry | None:
    """Return the Entry for a zip member, None for a no-op, or raise if it is unsafe."""
    parts = split_name(info.filename)
    if parts is None:
        raise unsafe(info.filename, "path leaves the update folder")
    if not parts:
        return None
    return Entry(parts, is_dir=info.is_dir())


def single_root(entries: list[Entry]) -> str:
    """Return the one top-level folder name shared by every entry, or raise."""
    if not entries:
        raise ApplyError("the update archive is empty")
    roots = {entry.parts[0] for entry in entries}
    if len(roots) != 1:
        raise ApplyError("the update archive must hold one top-level folder")
    root = roots.pop()
    if not any(len(entry.parts) > 1 or entry.is_dir for entry in entries):
        raise ApplyError("the update archive must hold one top-level folder")
    return root


def write_tar_member(tar: tarfile.TarFile, member: tarfile.TarInfo, target: Path, entry: Entry) -> None:
    """Write one checked tar member to target."""
    if entry.is_dir:
        target.mkdir(parents=True, exist_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if member.issym():
        target.symlink_to(member.linkname)
        return
    source = tar.extractfile(member)
    if source is None:
        raise ApplyError(f"could not read {member.name} from the update archive")
    with source, target.open("wb") as out:
        shutil.copyfileobj(source, out)
    target.chmod((member.mode & FILE_MODE_MASK) | OWNER_READ_WRITE)


def extract_tar(archive: Path, dest: Path) -> str:
    """Check every member of a tar.gz, then unpack it; return the top-level folder name."""
    with tarfile.open(archive, "r:gz") as tar:
        checked = [(member, check_tar_member(member)) for member in tar.getmembers()]
        entries = [entry for _, entry in checked if entry is not None]
        root = single_root(entries)
        for member, entry in checked:
            if entry is not None:
                write_tar_member(tar, member, dest.joinpath(*entry.parts), entry)
    return root


def extract_zip(archive: Path, dest: Path) -> str:
    """Check every entry of a zip, then unpack it; return the top-level folder name."""
    with zipfile.ZipFile(archive) as bundle:
        checked = [(info, check_zip_member(info)) for info in bundle.infolist()]
        entries = [entry for _, entry in checked if entry is not None]
        root = single_root(entries)
        for info, entry in checked:
            if entry is None:
                continue
            target = dest.joinpath(*entry.parts)
            if entry.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out)
    return root


def extract_archive(archive: Path, dest: Path) -> Path:
    """Unpack archive into dest and return its single top-level folder.

    Every member is checked before anything is written, so a refused archive
    leaves dest empty. dest should be a fresh staging folder the caller removes.
    """
    name = archive.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        unpack = extract_tar
    elif name.endswith(".zip"):
        unpack = extract_zip
    else:
        raise ApplyError(f"unsupported archive type: {archive.name}")
    try:
        root = unpack(archive, dest)
    except READ_ERRORS as exc:
        raise ApplyError(f"could not read or unpack {archive.name}: {exc}") from exc
    return dest / root


def old_install_path(install_dir: Path) -> Path:
    """Return the folder that keeps the previous version beside install_dir."""
    return install_dir.with_name(install_dir.name + OLD_SUFFIX)


def restore_old_install(install_dir: Path, old_dir: Path, cause: OSError) -> None:
    """Put the previous version back after a failed swap, then raise."""
    try:
        old_dir.rename(install_dir)
    except OSError as exc:
        raise ApplyError(
            f"the update failed ({cause}) and the previous version could not be put back; it is at {old_dir}: {exc}"
        ) from exc
    raise ApplyError(f"the update failed ({cause}); the previous version was restored") from cause


def swap_install(install_dir: Path, staged_dir: Path) -> None:
    """Replace install_dir with staged_dir, keeping the old copy beside it.

    The two folders must be on the same filesystem so each move is one atomic
    rename. If the new copy cannot be moved in, the old one is put back.
    """
    if not install_dir.is_dir():
        raise ApplyError(f"install folder not found: {install_dir}")
    old_dir = old_install_path(install_dir)
    try:
        if old_dir.exists():
            shutil.rmtree(old_dir)
        install_dir.rename(old_dir)
    except OSError as exc:
        raise ApplyError(f"could not move the current version aside: {exc}") from exc
    try:
        staged_dir.rename(install_dir)
    except OSError as exc:
        restore_old_install(install_dir, old_dir, exc)


def cleanup_old_install(install_dir: Path) -> None:
    """Delete the previous version left beside install_dir by the last swap."""
    old_dir = old_install_path(install_dir)
    if not old_dir.exists():
        return
    try:
        shutil.rmtree(old_dir)
    except OSError as exc:
        logger.warning("could not remove the previous version at %s: %s", old_dir, exc)
