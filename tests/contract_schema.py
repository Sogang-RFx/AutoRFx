"""Validate payloads against the JSON Schema files in /contract (CONTRACT-01, INV-9).

The schemas are generated from ``web/src/lib/rfq/contract.ts``. Backend output
is checked against the same files the web validates with, so the two sides
cannot drift apart silently.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACT_DIR = Path(__file__).resolve().parents[1] / "contract"


@cache
def validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def errors(name: str, payload: object) -> list[str]:
    """Human-readable violations of ``contract/<name>.schema.json``; empty when the payload conforms."""
    return [
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message[:200]}"
        for error in validator(name).iter_errors(payload)
    ]


def assert_valid(name: str, payload: object) -> None:
    found = errors(name, payload)
    assert not found, f"{name}: " + " | ".join(found[:5])
