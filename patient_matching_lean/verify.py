#!/usr/bin/env python3
"""Check Lean sources and every named theorem's transitive axiom dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from make_standalone import MODULES, ROOT, assemble

ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake", default="lake", help="Lake executable; default: lake on PATH")
    args = parser.parse_args()
    log: list[str] = []

    def run(arguments: list[str]) -> str:
        log.append("COMMAND " + json.dumps(arguments))
        result = subprocess.run(
            arguments, cwd=ROOT, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False,
        )
        log.append(result.stdout)
        log.append(f"EXIT_CODE {result.returncode}")
        if result.returncode != 0:
            raise RuntimeError(f"Command failed with exit code {result.returncode}")
        return result.stdout

    try:
        expected_source, names = assemble()
        proof_file = ROOT / "PatientMatchingProofs.lean"
        if proof_file.read_text(encoding="utf-8") != expected_source:
            raise RuntimeError("Standalone file differs from modular sources; run make_standalone.py")
        manifest = json.loads((ROOT / "proof_manifest.json").read_text(encoding="utf-8"))
        if manifest != {"lean_version": "4.19.0", "theorems": names}:
            raise RuntimeError("Proof manifest does not match the actual source declarations")
        # Hygiene is additional to compilation and the transitive kernel-axiom audit.
        prohibited = re.compile(r"\b(?:sorry|admit|native_decide|unsafe)\b|^\s*axiom\s", re.MULTILINE)
        for module in MODULES:
            content = (ROOT / "PatientMatching" / f"{module}.lean").read_text(encoding="utf-8")
            if prohibited.search(content):
                raise RuntimeError(f"Prohibited proof escape/declaration in {module}.lean")
        version = run([args.lake, "env", "lean", "--version"]).strip()
        if not version.startswith("Lean (version 4.19.0,"):
            raise RuntimeError(f"Unexpected toolchain: {version}")
        run([args.lake, "build"])
        audit = run([
            args.lake, "env", "lean", "-DwarningAsError=true", "PatientMatchingProofs.lean"
        ])
        observed: dict[str, list[str]] = {}
        for name, body in re.findall(r"'([^']+)' depends on axioms:\s*\[([^\]]*)\]", audit):
            dependencies = [item.strip() for item in body.split(",") if item.strip()]
            observed[name] = dependencies
        for name in re.findall(r"'([^']+)' does not depend on any axioms", audit):
            observed[name] = []
        if set(observed) != set(names):
            missing = sorted(set(names) - set(observed))
            extra = sorted(set(observed) - set(names))
            raise RuntimeError(f"Incomplete theorem audit; missing={missing}, extra={extra}")
        used_axioms = set().union(*(set(items) for items in observed.values()))
        if not used_axioms <= ALLOWED_AXIOMS:
            raise RuntimeError(f"Unapproved axioms: {sorted(used_axioms - ALLOWED_AXIOMS)}")
        if "sorryAx" in audit:
            raise RuntimeError("Open proof detected in transitive dependency audit")
        relevant = [
            ROOT / "PatientMatching" / f"{module}.lean" for module in MODULES
        ] + [proof_file, ROOT / "PatientMatching.lean", ROOT / "lean-toolchain", ROOT / "lakefile.toml"]
        summary = {
            "status": "passed",
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "lean": version,
            "named_theorem_count": len(names),
            "custom_axioms": [],
            "standard_axioms_observed": sorted(used_axioms),
            "theorem_axioms": observed,
            "source_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in relevant
            },
            "scope": "Abstract functionality, guarded state transitions, conditional finite-game privacy, and impossibility results; no deployed-cryptography verification.",
        }
        (ROOT / "verification_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        log.append(f"PASS: {len(names)} named theorems audited; no custom or unapproved axioms.")
        print(log[-1])
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        log.append(f"FAIL: {error}")
        print(log[-1], file=sys.stderr)
        return 1
    finally:
        (ROOT / "validation.log").write_text("\n".join(log) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
