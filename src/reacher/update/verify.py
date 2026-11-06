"""Checks that decide whether a downloaded update is trusted."""

from __future__ import annotations

import hashlib
import hmac
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .release_key import load_release_public_key

HASH_CHUNK_BYTES = 1024 * 1024
SUMS_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")


class VerificationError(RuntimeError):
    """Raised when an update fails a signature or hash check."""


def verify_signature(sums: bytes, signature: bytes, public_key: Ed25519PublicKey | None = None) -> None:
    """Raise VerificationError unless signature is a valid signature of sums.

    The shipped release key is used unless a key is passed in.
    """
    key = public_key if public_key is not None else load_release_public_key()
    try:
        key.verify(signature, sums)
    except InvalidSignature as exc:
        raise VerificationError("the release signature is not valid") from exc


def parse_sums(text: str) -> dict[str, str]:
    """Return the name to lowercase hash map in SHA256SUMS text.

    Every line must be ``<64 hex>  <name>`` (or ``<64 hex> *<name>``). Names that
    are paths or that appear twice are refused, because the list is trusted.
    """
    hashes: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = SUMS_LINE.match(line)
        if match is None:
            raise VerificationError(f"SHA256SUMS line {number} is malformed")
        digest, name = match.group(1).lower(), match.group(2)
        if "/" in name or "\\" in name or name in (".", ".."):
            raise VerificationError(f"SHA256SUMS line {number} names a path, not a file")
        if name in hashes:
            raise VerificationError(f"{name} is listed twice in SHA256SUMS")
        hashes[name] = digest
    if not hashes:
        raise VerificationError("SHA256SUMS has no entries")
    return hashes


def expected_hash(text: str, asset_name: str) -> str:
    """Return the signed hash for asset_name, or raise if the release did not list it."""
    hashes = parse_sums(text)
    if asset_name not in hashes:
        raise VerificationError(f"{asset_name} is not listed in SHA256SUMS")
    return hashes[asset_name]


def verify_file_hash(path: Path, expected: str) -> None:
    """Raise VerificationError unless the file at path has the expected SHA-256."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise VerificationError(f"could not read {path.name} to check its hash: {exc}") from exc
    if not hmac.compare_digest(digest.hexdigest(), expected.lower()):
        raise VerificationError(f"the hash of {path.name} does not match the signed list")
