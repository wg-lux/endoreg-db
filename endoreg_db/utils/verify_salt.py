from __future__ import annotations

import base64
import binascii
import math
import re

# Minimum raw cryptographic key size (256 bits = 32 bytes)
MIN_KEY_BYTES = 32

# Known weak patterns and placeholder values
BLOCKED_PATTERNS = (
    "default",
    "change",
    "secret",
    "password",
    "django-insecure",
    "endoreg",
    "test",
    "sample",
    "placeholder",
)


def _calculate_shannon_entropy(data: bytes) -> float:
    """Calculate the Shannon entropy of a byte sequence (0.0 to 8.0 bits/byte)."""
    if not data:
        return 0.0
    frequencies = [data.count(b) / len(data) for b in set(data)]
    return -sum(p * math.log2(p) for p in frequencies)


def validate_identity_salt(raw_value: object) -> str:
    """
    Validate strength without changing the established UTF-8 key material.

    Accepts:
      - 64+ character hex string (32+ bytes)
      - 44+ character base64 string (32+ bytes)
      - 32+ bytes UTF-8 secret material

    Raises:
      ValueError: If the key fails length, format, or entropy requirements.
                  (Never echoes the secret into exception messages).
    """
    if not isinstance(raw_value, (str, bytes)):
        raise ValueError("Identity salt must be UTF-8 text or bytes.")
    try:
        value = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else raw_value
    except UnicodeError:
        raise ValueError("Identity salt must be UTF-8 text or bytes.") from None
    if not value or value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError("Identity salt must be a single nonblank line.")

    # 1. Normalize and decode to raw bytes
    key_bytes: bytes
    cleaned = value
    # Check for banned substrings/defaults (case-insensitive)
    lower_val = cleaned.lower()
    if any(pat in lower_val for pat in BLOCKED_PATTERNS):
        raise ValueError(
            "Identity salt contains a forbidden default or insecure placeholder pattern."
        )

    # Attempt Hex decode (typical for 64-char openssl outputs)
    if len(cleaned) >= 64 and re.fullmatch(r"^[0-9a-fA-F]+$", cleaned):
        try:
            key_bytes = bytes.fromhex(cleaned)
        except ValueError:
            raise ValueError("Identity salt contains malformed hex material.") from None
    # Attempt Base64 decode
    elif len(cleaned) >= 44 and re.fullmatch(r"^[A-Za-z0-9+/=_-]+$", cleaned):
        try:
            # Add padding if needed
            padded = cleaned + "=" * (-len(cleaned) % 4)
            key_bytes = base64.b64decode(padded, altchars=b"-_", validate=True)
        except binascii.Error:
            raise ValueError(
                "Identity salt contains malformed base64 material."
            ) from None
    else:
        # Fall back to raw UTF-8 string bytes
        key_bytes = cleaned.encode("utf-8")

    # 2. Strict Raw Length Verification
    if len(key_bytes) < MIN_KEY_BYTES:
        raise ValueError(
            f"Identity salt is cryptographically too short: decoded to {len(key_bytes)} bytes; "
            f"minimum required is {MIN_KEY_BYTES} bytes (256 bits)."
        )

    # 3. Minimum Character / Byte Diversity
    unique_bytes = len(set(key_bytes))
    if unique_bytes < 16:
        raise ValueError(
            f"Identity salt lacks byte diversity: contains only {unique_bytes} distinct byte values. "
            "A CSPRNG-generated key is required."
        )

    # 4. Shannon Entropy Check
    # A uniformly distributed 32-byte CSPRNG sequence typically scores ~4.5 - 5.0 bits/byte.
    # Repetitive or simple ASCII phrases score well below 3.5.
    entropy = _calculate_shannon_entropy(key_bytes)
    if entropy < 3.5:
        raise ValueError(
            f"Identity salt failed entropy threshold (measured {entropy:.2f} bits/byte, "
            "minimum required: 3.50). Rejecting predictable or patterned key."
        )

    return value
