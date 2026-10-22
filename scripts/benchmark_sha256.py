"""Bounded, streaming candidate search; no Django setup or persistent writes."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import itertools
import json
import math
import string
from collections.abc import Callable
from dataclasses import asdict, dataclass
from time import perf_counter


ALGORITHMS = ("hmac-sha256", "sha256", "salt-prefix", "salt-suffix")


def make_digest(algorithm: str, salt: str) -> Callable[[str], bytes]:
    """Match exact UTF-8 bytes, without normalization or implicit separators."""
    key = salt.encode("utf-8")
    if algorithm == "hmac-sha256":
        return lambda value: hmac.digest(key, value.encode("utf-8"), "sha256")
    if algorithm == "sha256":
        if salt:
            raise ValueError("Plain SHA-256 requires an empty salt")
        return lambda value: hashlib.sha256(value.encode("utf-8")).digest()
    if algorithm == "salt-prefix":
        return lambda value: hashlib.sha256(key + value.encode("utf-8")).digest()
    if algorithm == "salt-suffix":
        return lambda value: hashlib.sha256(value.encode("utf-8") + key).digest()
    raise ValueError(f"Unsupported algorithm: {algorithm}")


@dataclass(frozen=True)
class SearchResult:
    status: str
    candidate: str | None
    attempts: int
    search_space: int
    elapsed_seconds: float
    candidates_per_second: float
    estimated_full_search_seconds: float | None


def search(
    target: str,
    digest: Callable[[str], bytes],
    *,
    charset: str,
    min_length: int,
    max_length: int,
    prefix: str = "",
    suffix: str = "",
    max_seconds: float = 10.0,
    max_attempts: int = 1_000_000,
) -> SearchResult:
    """Enumerate lengths then character products, stopping at the first match."""
    if len(target) != 64 or any(c not in string.hexdigits for c in target):
        raise ValueError("Target must contain exactly 64 hexadecimal characters")
    if not charset or len(set(charset)) != len(charset):
        raise ValueError("Charset must be nonempty and contain unique characters")
    if not 0 <= min_length <= max_length <= 256:
        raise ValueError("Lengths must satisfy 0 <= min_length <= max_length <= 256")
    if not math.isfinite(max_seconds) or max_seconds <= 0 or max_attempts <= 0:
        raise ValueError("Time and attempt limits must be positive and finite")
    expected = bytes.fromhex(target)
    space = sum(len(charset) ** length for length in range(min_length, max_length + 1))
    attempts = 0
    match: str | None = None
    status = "exhausted"
    start = perf_counter()
    try:
        for length in range(min_length, max_length + 1):
            for characters in itertools.product(charset, repeat=length):
                if attempts >= max_attempts:
                    status = "attempt_limit"
                    break
                if perf_counter() - start >= max_seconds:
                    status = "time_limit"
                    break
                candidate = "".join(characters)
                actual = digest(prefix + candidate + suffix)
                attempts += 1
                if hmac.compare_digest(actual, expected):
                    match, status = candidate, "found"
                    break
            if status != "exhausted":
                break
    except KeyboardInterrupt:
        status = "interrupted"
    elapsed = perf_counter() - start
    rate = attempts / elapsed if elapsed > 0 else 0.0
    try:
        estimate = space / rate if rate else None
    except OverflowError:
        estimate = None
    return SearchResult(status, match, attempts, space, elapsed, rate, estimate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", choices=ALGORITHMS, default="hmac-sha256")
    parser.add_argument(
        "--salt", required=True, help="Exact UTF-8 key/salt; empty for plain SHA-256"
    )
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--target", help="64-character hexadecimal digest")
    targets.add_argument(
        "--plaintext", help="Generate a target from prefix + plaintext + suffix"
    )
    parser.add_argument("--charset", default=string.digits)
    parser.add_argument("--min-length", type=int, default=1)
    parser.add_argument("--max-length", type=int, default=6)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--max-seconds", type=float, default=10.0)
    parser.add_argument("--max-attempts", type=int, default=1_000_000)
    args = parser.parse_args(argv)
    try:
        digest = make_digest(args.algorithm, args.salt)
        target = (
            digest(args.prefix + args.plaintext + args.suffix).hex()
            if args.plaintext is not None
            else args.target
        )
        result = search(
            target,
            digest,
            charset=args.charset,
            min_length=args.min_length,
            max_length=args.max_length,
            prefix=args.prefix,
            suffix=args.suffix,
            max_seconds=args.max_seconds,
            max_attempts=args.max_attempts,
        )
    except (ValueError, UnicodeError) as error:
        parser.error(str(error))
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.status == "found" else 1


if __name__ == "__main__":
    raise SystemExit(main())
