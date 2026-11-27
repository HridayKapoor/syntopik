"""Syntopik — passphrase encryption for multi-device sync deltas.

Sync deltas carry a learner's whole study history (reviews, mistakes, profile).
When they travel through a shared drive, chat or email they should be
unreadable without the learner's passphrase. This wraps the existing
`syntopik-delta-v1` JSON in a `syntopik-delta-enc-v1` envelope:

- Key derivation: `scrypt` (Python stdlib `hashlib.scrypt`), random 16-byte
  salt, N=2^15, r=8, p=1 (~32 MiB, ~0.1 s), 32-byte key.
- Cipher: AES-256-GCM (authenticated) from the optional `cryptography`
  package (`pip install "syntopik[sync]"`), random 12-byte nonce.
- The envelope header (format, KDF parameters, cipher) is bound as
  associated data, so editing any header field makes decryption fail.

Decryption is fail-closed: a wrong passphrase, a tampered byte, or KDF
parameters outside safe bounds all raise `DeltaCryptoError` and nothing is
merged. Plain (unencrypted) deltas keep working unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any

__all__ = [
    "ENCRYPTED_FORMAT",
    "PASSPHRASE_ENV",
    "DeltaCryptoError",
    "crypto_available",
    "is_encrypted_envelope",
    "encrypt_delta_bytes",
    "decrypt_delta_envelope",
    "passphrase_from_env",
]

ENCRYPTED_FORMAT = "syntopik-delta-enc-v1"
CIPHER_NAME = "AES-256-GCM"
PASSPHRASE_ENV = "SYNTOPIK_SYNC_PASSPHRASE"
MIN_PASSPHRASE_CHARS = 8

_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
# Bounds accepted when *reading* a file, so a crafted header cannot demand
# gigabytes of memory or minutes of CPU.
_MAX_SCRYPT_N = 2**18
_MAX_SCRYPT_R = 16
_MAX_SCRYPT_P = 4
_SALT_BYTES = 16
_NONCE_BYTES = 12
_KEY_BYTES = 32
_INSTALL_HINT = 'Encrypted sync needs the optional "cryptography" package: pip install "syntopik[sync]"'


class DeltaCryptoError(ValueError):
    """Raised when an encrypted delta cannot be produced or opened."""


def crypto_available() -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception:
        return False
    return True


def _aesgcm(key: bytes) -> Any:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:  # pragma: no cover - exercised when the extra is absent
        raise DeltaCryptoError(_INSTALL_HINT) from exc
    return AESGCM(key)


def passphrase_from_env() -> str | None:
    val = os.environ.get(PASSPHRASE_ENV)
    return val if val else None


def is_encrypted_envelope(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("format") == ENCRYPTED_FORMAT


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    if not isinstance(passphrase, str) or not passphrase:
        raise DeltaCryptoError("A passphrase is required for encrypted sync deltas.")
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=128 * r * (n + p + 2) + 1024 * 1024,
        dklen=_KEY_BYTES,
    )


def _header(kdf: dict[str, Any]) -> dict[str, Any]:
    return {"format": ENCRYPTED_FORMAT, "cipher": CIPHER_NAME, "kdf": kdf}


def _aad(header: dict[str, Any]) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def encrypt_delta_bytes(plaintext: bytes, passphrase: str) -> dict[str, Any]:
    """Encrypt serialized delta JSON and return the `syntopik-delta-enc-v1` envelope."""
    if len(passphrase or "") < MIN_PASSPHRASE_CHARS:
        raise DeltaCryptoError(f"Use a passphrase of at least {MIN_PASSPHRASE_CHARS} characters.")
    salt = secrets.token_bytes(_SALT_BYTES)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    kdf = {
        "name": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": base64.b64encode(salt).decode("ascii"),
    }
    header = _header(kdf)
    key = _derive_key(passphrase, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    ciphertext = _aesgcm(key).encrypt(nonce, plaintext, _aad(header))
    return {
        **header,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def _b64(field: str, value: Any, expected_len: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise DeltaCryptoError(f"Encrypted delta field '{field}' is missing.")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise DeltaCryptoError(f"Encrypted delta field '{field}' is not valid base64.") from exc
    if expected_len is not None and len(raw) != expected_len:
        raise DeltaCryptoError(f"Encrypted delta field '{field}' has the wrong length.")
    return raw


def decrypt_delta_envelope(envelope: dict[str, Any], passphrase: str | None) -> bytes:
    """Return the plaintext delta bytes, or raise `DeltaCryptoError` (fail-closed)."""
    if not is_encrypted_envelope(envelope):
        raise DeltaCryptoError("Not an encrypted sync delta.")
    if envelope.get("cipher") != CIPHER_NAME:
        raise DeltaCryptoError(f"Unsupported cipher: {envelope.get('cipher')!r}")
    if not passphrase:
        raise DeltaCryptoError(f"This sync delta is encrypted. Provide the passphrase (or set {PASSPHRASE_ENV}).")
    kdf = envelope.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("name") != "scrypt":
        raise DeltaCryptoError("Unsupported key-derivation settings.")
    try:
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    except Exception as exc:
        raise DeltaCryptoError("Malformed key-derivation settings.") from exc
    if not (2**10 <= n <= _MAX_SCRYPT_N and n & (n - 1) == 0 and 1 <= r <= _MAX_SCRYPT_R and 1 <= p <= _MAX_SCRYPT_P):
        raise DeltaCryptoError("Key-derivation settings are outside safe bounds.")
    salt = _b64("kdf.salt", kdf.get("salt"), _SALT_BYTES)
    nonce = _b64("nonce", envelope.get("nonce"), _NONCE_BYTES)
    ciphertext = _b64("ciphertext", envelope.get("ciphertext"))
    header = _header({"name": "scrypt", "n": n, "r": r, "p": p, "salt": kdf.get("salt")})
    key = _derive_key(passphrase, salt, n, r, p)
    try:
        return _aesgcm(key).decrypt(nonce, ciphertext, _aad(header))
    except DeltaCryptoError:
        raise
    except Exception as exc:
        raise DeltaCryptoError("Wrong passphrase, or the encrypted delta was modified.") from exc
