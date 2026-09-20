#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("github-classic-pat", re.compile(r"\bghp_[A-Za-z0-9]{30,}\b")),
    ("github-fine-grained-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
]

SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".zip", ".gz", ".tgz", ".7z", ".tar", ".pdf",
}
MAX_FILE_BYTES = 2 * 1024 * 1024


def tracked_files(repo: Path) -> list[Path]:
    cp = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [
        repo / raw.decode("utf-8", errors="surrogateescape")
        for raw in cp.stdout.split(b"\0")
        if raw
    ]


def scan_file(path: Path) -> list[tuple[str, int]]:
    try:
        if path.suffix.lower() in SKIP_SUFFIXES:
            return []
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    findings: list[tuple[str, int]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((name, lineno))
    return findings


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = p.parse_args()
    repo = args.repo_root.resolve()

    hits: list[tuple[str, Path, int]] = []
    for path in tracked_files(repo):
        if not path.is_file():
            continue
        for name, lineno in scan_file(path):
            hits.append((name, path.relative_to(repo), lineno))

    if hits:
        print("AEGIS SECRET GUARD: BLOCKED")
        for name, rel, lineno in hits:
            print(f"{rel}:{lineno}: secret-pattern={name}")
        print("Secret values are intentionally not printed.")
        return 2

    print("AEGIS SECRET GUARD: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
