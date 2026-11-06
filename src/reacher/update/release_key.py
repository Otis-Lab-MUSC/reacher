"""The public key every installed app trusts for update signatures."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

RELEASE_PUBLIC_KEY_B64 = "wXLpAxeYXYwjOykUoImY8cz0L8Kj7hUGWsxg41uOuDo="


def load_release_public_key() -> Ed25519PublicKey:
    """Return the shipped release public key."""
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(RELEASE_PUBLIC_KEY_B64, validate=True))
