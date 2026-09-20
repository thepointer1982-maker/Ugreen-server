#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(
    os.environ.get("AEGIS_REPO_ROOT", Path(__file__).resolve().parents[1])
).resolve()
REGISTRY = Path(
    os.environ.get(
        "AEGIS_LOCAL_MODEL_REGISTRY",
        REPO_ROOT / "config" / "models" / "aegis-local-models.json",
    )
)
OLLAMA_TAGS_URL = os.environ.get(
    "AEGIS_OLLAMA_TAGS_URL",
    "http://127.0.0.1:11434/api/tags",
)

ROLE_KEYWORDS = {
    "reasoning": (
        "warum", "reason", "analyse", "analyze", "architecture",
        "architektur", "root cause", "ursache", "debug", "diagnose",
        "compare", "vergleich",
    ),
    "coding": (
        "code", "implement", "script", "python", "bash", "powershell",
        "refactor", "test", "fix", "patch", "compile",
    ),
}


def read_registry() -> dict[str, Any]:
    value = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if value.get("schema") != "aegis-local-model-registry/v1":
        raise ValueError("invalid model registry schema")
    return value


def installed_models() -> set[str]:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=2) as response:
            data = json.load(response)
    except Exception:
        return set()

    result: set[str] = set()
    for row in data.get("models", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        model = str(row.get("model") or "")
        for value in (name, model):
            if value:
                result.add(value)
                result.add(value.split("@", 1)[0])
    return result


def infer_role(task: str) -> str:
    text = task.lower()
    reasoning = sum(text.count(token) for token in ROLE_KEYWORDS["reasoning"])
    coding = sum(text.count(token) for token in ROLE_KEYWORDS["coding"])
    return "reasoning" if reasoning > coding else "coding"


def choose_model(
    registry: dict[str, Any],
    installed: set[str],
    *,
    role: str,
) -> dict[str, Any]:
    models = [
        row
        for row in registry.get("models", [])
        if isinstance(row, dict) and row.get("auto_select") is True
    ]
    by_id = {str(row.get("id")): row for row in models}

    policy = registry.get("policy", {})
    primary = str(policy.get("primary_coding_model") or "")
    reasoning = str(policy.get("reasoning_model") or "")

    preferred = reasoning if role in {
        "reasoning", "debug", "architecture", "root-cause"
    } else primary

    if preferred and preferred in installed and preferred in by_id:
        return {
            "status": "selected",
            "model": preferred,
            "role": role,
            "reason": "preferred-role-model-installed",
        }

    if primary and primary in installed and primary in by_id:
        return {
            "status": "selected",
            "model": primary,
            "role": role,
            "reason": "primary-local-fallback",
        }

    for row in models:
        model_id = str(row.get("id") or "")
        roles = {str(x) for x in row.get("roles", [])}
        if model_id in installed and role in roles:
            return {
                "status": "selected",
                "model": model_id,
                "role": role,
                "reason": "role-compatible-local-fallback",
            }

    return {
        "status": "blocked",
        "model": None,
        "role": role,
        "reason": "no-registered-local-model-installed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="")
    parser.add_argument(
        "--role",
        choices=[
            "coding", "reasoning", "debug", "architecture",
            "root-cause", "general",
        ],
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    registry = read_registry()
    role = args.role or infer_role(args.task)
    result = choose_model(registry, installed_models(), role=role)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    elif result.get("model"):
        print(result["model"])

    return 0 if result.get("status") == "selected" else 5


if __name__ == "__main__":
    raise SystemExit(main())
