"""The unseal's one-time signature (decision D27; security review of #65, H-2).

Lamport's one-time signature over SHA-256, with the standard library's ``hashlib`` and
``hmac`` only. The owner's passphrase derives the scrypt output whose SHA-256 the key record
holds as its verifier; the same output also derives, deterministically, 256 pairs of
secrets, and the key record commits to the SHA-256 of their public key (``signer_sha256``)
beside the verifier. The unseal entry carries a signature over its own canonical bytes,
everything but the signature (the candidate, the commit, the accepted diff hash and the
previous entry's hash among them), and the scrypt output is never revealed. So a copy of the
entry authorises nothing but that exact entry: change any field and the signature no longer
verifies. The owner's steps do not change: one passphrase, typed once.

Every value is domain separated from the others and from the verifier, which is the plain
SHA-256 of the scrypt output:

* the seed is HMAC-SHA256 keyed with the scrypt output, of ``DOMAIN + b"seed"``;
* secret ``(i, b)``, for ``i`` from 0 to 255 and ``b`` 0 or 1, is HMAC-SHA256 keyed with the
  seed, of ``DOMAIN + b"secret"``, then ``i`` as two bytes, big-endian, then ``b`` as one byte;
* its public half is the SHA-256 of ``DOMAIN + b"leaf"`` and the secret;
* ``signer_sha256`` is the lower-case hex SHA-256 of ``DOMAIN + b"public"`` and the 512
  public halves in order, ``i`` ascending and ``b`` 0 before 1;
* the digest signed is the SHA-256 of ``DOMAIN + b"message"`` and the entry's canonical
  bytes, and its bit ``i`` (byte ``i // 8``, most significant bit first) is the secret of pair
  ``i`` the signature reveals.

A signature is ``{"preimages": [...], "complements": [...]}``, two lists of 256 lower-case
hex strings of 64 characters: for each pair, the secret revealed, and the public half of the
secret not revealed. Verification hashes each preimage, places the two halves of each pair by
the digest's bit, and compares the public key's hash with ``signer_sha256`` in constant time.

One signature reveals one secret of each pair and is safe. Two signatures over different
bytes reveal both secrets wherever their digests differ, about half the pairs, and each
further one weakens the key quickly. So the bytes the unseal signs are fixed by the ledger it
follows (its timestamp is its predecessor's; ``gitledger``): a retry after a failed push, on
the same ledger at the same commit, signs the same bytes and reveals nothing new. Nothing
else is ever signed with this key.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping

DOMAIN = b"norse-stack/holdout-unseal/lamport-sha256/v1/"
BITS = 256
SIGNATURE_KEYS = frozenset({"preimages", "complements"})
_HEX32 = re.compile(r"[0-9a-f]{64}")


def seed_of(scrypt_output: bytes) -> bytes:
    """The signing seed the scrypt output derives; never the output itself."""
    return hmac.new(scrypt_output, DOMAIN + b"seed", hashlib.sha256).digest()


def _secret(seed: bytes, index: int, bit: int) -> bytes:
    message = DOMAIN + b"secret" + index.to_bytes(2, "big") + bytes([bit])
    return hmac.new(seed, message, hashlib.sha256).digest()


def _leaf(secret: bytes) -> bytes:
    return hashlib.sha256(DOMAIN + b"leaf" + secret).digest()


def _public_hash(pairs) -> str:
    public = hashlib.sha256(DOMAIN + b"public")
    for zero, one in pairs:
        public.update(zero)
        public.update(one)
    return public.hexdigest()


def signer_sha256(seed: bytes) -> str:
    """The SHA-256 of the public key, which the key record commits to."""
    return _public_hash(
        (_leaf(_secret(seed, i, 0)), _leaf(_secret(seed, i, 1))) for i in range(BITS)
    )


def digest_bits(message: bytes) -> list[int]:
    """The 256 bits of the digest signed for ``message``, most significant first."""
    digest = hashlib.sha256(DOMAIN + b"message" + message).digest()
    return [(digest[i // 8] >> (7 - i % 8)) & 1 for i in range(BITS)]


def sign(seed: bytes, message: bytes) -> dict:
    """The one-time signature of ``message``. Sign the unseal's bytes and nothing else."""
    bits = digest_bits(message)
    return {
        "preimages": [_secret(seed, i, bit).hex() for i, bit in enumerate(bits)],
        "complements": [_leaf(_secret(seed, i, 1 - bit)).hex() for i, bit in enumerate(bits)],
    }


def signature_shape_error(signature: object) -> str | None:
    """Why ``signature`` is not shaped as a signature, or None."""
    if not isinstance(signature, Mapping) or set(signature) != SIGNATURE_KEYS:
        return f"the unseal's signature is not an object holding exactly {sorted(SIGNATURE_KEYS)}"
    for name in sorted(SIGNATURE_KEYS):
        values = signature[name]
        if not isinstance(values, list) or len(values) != BITS or not all(
            isinstance(v, str) and _HEX32.fullmatch(v) for v in values
        ):
            return (
                f"the unseal's signature {name} is not {BITS} lower-case hex strings of 64 "
                "characters"
            )
    return None


def signature_error(signer: str, message: bytes, signature: object) -> str | None:
    """Why ``signature`` is not a signature of ``message`` under the public key whose
    SHA-256 is ``signer``, or None. Compared in constant time."""
    problem = signature_shape_error(signature)
    if problem:
        return problem
    pairs = []
    for i, bit in enumerate(digest_bits(message)):
        shown = _leaf(bytes.fromhex(signature["preimages"][i]))
        other = bytes.fromhex(signature["complements"][i])
        pairs.append((shown, other) if bit == 0 else (other, shown))
    if not hmac.compare_digest(_public_hash(pairs), signer):
        return (
            "its signature does not verify against the signer the key record genesis pins: "
            "the owner's key did not sign these bytes, or they changed after it did (D27)"
        )
    return None
