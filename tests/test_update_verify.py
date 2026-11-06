"""Signature and hash checks that decide whether a downloaded update is trusted."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from reacher.update.release_key import RELEASE_PUBLIC_KEY_B64, load_release_public_key
from reacher.update.verify import (
    VerificationError,
    expected_hash,
    parse_sums,
    verify_file_hash,
    verify_signature,
)

ASSET = "labrynth-3.0.1-beta.17-linux-x64.tar.gz"
OTHER = "labrynth-3.0.1-beta.17-windows-x64.zip"


def sums_text(**hashes: str) -> str:
    """Build SHA256SUMS text in the two-space format sha256sum writes."""
    return "".join(f"{digest}  {name}\n" for name, digest in hashes.items())


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def test_the_shipped_public_key_is_a_valid_32_byte_ed25519_key() -> None:
    assert len(base64.b64decode(RELEASE_PUBLIC_KEY_B64, validate=True)) == 32
    assert isinstance(load_release_public_key(), Ed25519PublicKey)


def test_a_valid_signature_is_accepted(key: Ed25519PrivateKey) -> None:
    sums = sums_text(**{ASSET: "a" * 64}).encode()

    verify_signature(sums, key.sign(sums), key.public_key())


def test_a_tampered_sums_file_is_rejected(key: Ed25519PrivateKey) -> None:
    sums = sums_text(**{ASSET: "a" * 64}).encode()
    signature = key.sign(sums)

    with pytest.raises(VerificationError, match="signature"):
        verify_signature(sums.replace(b"a", b"b", 1), signature, key.public_key())


def test_a_signature_from_another_key_is_rejected(key: Ed25519PrivateKey) -> None:
    sums = sums_text(**{ASSET: "a" * 64}).encode()
    stranger = Ed25519PrivateKey.generate()

    with pytest.raises(VerificationError, match="signature"):
        verify_signature(sums, stranger.sign(sums), key.public_key())


@pytest.mark.parametrize("signature", [b"", b"short", b"x" * 63, b"x" * 65])
def test_a_malformed_signature_is_rejected_not_crashed_on(key: Ed25519PrivateKey, signature: bytes) -> None:
    with pytest.raises(VerificationError, match="signature"):
        verify_signature(b"anything", signature, key.public_key())


def test_the_default_key_is_the_shipped_key(key: Ed25519PrivateKey) -> None:
    sums = b"anything"

    with pytest.raises(VerificationError, match="signature"):
        verify_signature(sums, key.sign(sums))


def test_parse_sums_maps_names_to_hashes() -> None:
    parsed = parse_sums(sums_text(**{ASSET: "a" * 64, OTHER: "B" * 64}))

    assert parsed == {ASSET: "a" * 64, OTHER: "b" * 64}


def test_parse_sums_accepts_the_binary_mode_marker() -> None:
    assert parse_sums(f"{'c' * 64} *{ASSET}\n") == {ASSET: "c" * 64}


@pytest.mark.parametrize(
    "line",
    ["", "not a sums line", f"{'a' * 63}  {ASSET}", f"{'g' * 64}  {ASSET}", f"{'a' * 64} {ASSET}"],
)
def test_parse_sums_rejects_a_malformed_line(line: str) -> None:
    with pytest.raises(VerificationError, match="SHA256SUMS"):
        parse_sums(f"{'a' * 64}  ok.txt\n{line}\n")


def test_parse_sums_rejects_a_name_that_is_a_path() -> None:
    with pytest.raises(VerificationError, match="SHA256SUMS"):
        parse_sums(f"{'a' * 64}  ../../etc/passwd\n")


def test_parse_sums_rejects_a_name_listed_twice() -> None:
    with pytest.raises(VerificationError, match="twice"):
        parse_sums(sums_text(**{ASSET: "a" * 64}) + f"{'b' * 64}  {ASSET}\n")


def test_expected_hash_finds_the_asset() -> None:
    assert expected_hash(sums_text(**{ASSET: "a" * 64, OTHER: "b" * 64}), OTHER) == "b" * 64


def test_expected_hash_refuses_an_asset_the_release_did_not_sign() -> None:
    with pytest.raises(VerificationError, match="not listed"):
        expected_hash(sums_text(**{OTHER: "b" * 64}), ASSET)


def test_a_file_with_the_listed_hash_passes(tmp_path: Path) -> None:
    path = tmp_path / ASSET
    path.write_bytes(b"payload" * 100_000)

    verify_file_hash(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_a_file_with_a_different_hash_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / ASSET
    path.write_bytes(b"payload")

    with pytest.raises(VerificationError, match="hash"):
        verify_file_hash(path, hashlib.sha256(b"something else").hexdigest())


def test_hash_comparison_ignores_the_case_of_the_listed_hash(tmp_path: Path) -> None:
    path = tmp_path / ASSET
    path.write_bytes(b"payload")

    verify_file_hash(path, hashlib.sha256(b"payload").hexdigest().upper())


def test_a_missing_file_is_a_verification_error(tmp_path: Path) -> None:
    with pytest.raises(VerificationError, match="read"):
        verify_file_hash(tmp_path / "nope", "a" * 64)


@pytest.mark.parametrize("text", ["", "\n", "   \n"])
def test_parse_sums_rejects_a_file_with_no_entries(text: str) -> None:
    with pytest.raises(VerificationError, match="SHA256SUMS"):
        parse_sums(text)
