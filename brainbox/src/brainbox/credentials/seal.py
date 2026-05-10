"""age sealing primitives — X25519 envelope encryption for credential bundles."""

from __future__ import annotations

from pyrage import decrypt, encrypt, x25519

BUNDLE_FORMAT_VERSION = 1


def generate_identity() -> tuple[str, str]:
    """Generate an X25519 keypair. Returns (recipient_pubkey, identity_secret) as age strings."""
    identity = x25519.Identity.generate()
    return str(identity.to_public()), str(identity)


def recipient_of(identity_str: str) -> str:
    """Derive the recipient pubkey from an identity secret."""
    return str(x25519.Identity.from_str(identity_str).to_public())


def seal(plaintext: bytes, recipient_str: str) -> bytes:
    """Seal bytes to an age recipient pubkey."""
    recipient = x25519.Recipient.from_str(recipient_str)
    return encrypt(plaintext, [recipient])


def unseal(ciphertext: bytes, identity_str: str) -> bytes:
    """Unseal bytes with an age identity secret."""
    identity = x25519.Identity.from_str(identity_str)
    return decrypt(ciphertext, [identity])
