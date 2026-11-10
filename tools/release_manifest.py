#!/usr/bin/env python3
"""Releasemanifest maken: digest en bron-commit invullen (release-CI, contract 1.3 §9).

``digest_pending_release`` mag alleen vóór de eerste release van een eigen image geaccepteerd zijn. Met de digest
ingevuld treedt die waarschuwing niet meer op; de acceptatie wordt daarom uit ``accepted_findings`` gehaald (anders
faalt ``jl-manifest validate --strict`` terecht op ``accepted_finding_unused``). Tekstbewerking, zodat de commentaren
van het manifest blijven staan.

    python tools/release_manifest.py <bron.yaml> <doel.yaml> --version 0.1.0 --digest sha256:<64 hex> --commit <40 hex>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _drop_pending_acceptance(text: str) -> str:
    """Haal de ``digest_pending_release``-regel uit ``accepted_findings`` (en het blok als het daarna leeg is)."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("accepted_findings:"):
            block = [line]
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i])
                i += 1
            items: list[list[str]] = []
            for b in block[1:]:
                if b.lstrip().startswith("- "):
                    items.append([b])
                elif items:
                    items[-1].append(b)
            kept = [it for it in items if "digest_pending_release" not in it[0]]
            if kept:
                out.append(line)
                out.extend(x for it in kept for x in it)
            continue
        out.append(line)
        i += 1
    return "".join(out)


def make_release_manifest(source: str, *, version: str, digest: str, commit: str) -> str:
    if not DIGEST.match(digest) or not COMMIT.match(commit):
        raise ValueError("digest of commit heeft niet de verwachte vorm")
    if f'tag: "{version}"' not in source:
        raise ValueError("manifest-tag ≠ releaseversie")
    if "  digest: null" not in source:
        raise ValueError("het bronmanifest heeft al een digest")
    out = source.replace("  digest: null", f'  digest: "{digest}"', 1)
    out = out.replace("  source_commit: null", f'  source_commit: "{commit}"', 1)
    return _drop_pending_acceptance(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path)
    p.add_argument("target", type=Path)
    p.add_argument("--version", required=True)
    p.add_argument("--digest", required=True)
    p.add_argument("--commit", required=True)
    args = p.parse_args(argv)
    try:
        text = make_release_manifest(
            args.source.read_text(encoding="utf-8"), version=args.version, digest=args.digest, commit=args.commit
        )
    except ValueError as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
