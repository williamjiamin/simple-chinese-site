"""Validator for the intentionally small, bundled JSON-schema vocabulary.

No reference resolution, code evaluation or network access is supported.
Structural bounds live in one schema file; semantic pinyin checks live in the
language module. Unsupported schema keywords fail instead of being ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "pinyin.phrases.schema.json"
_KEYWORDS = {"$schema", "$id", "title", "description", "default", "type", "const", "required",
             "additionalProperties", "properties", "items", "minItems", "maxItems",
             "minLength", "maxLength", "pattern"}


def _check(value: Any, schema: dict[str, Any], location: str) -> None:
    if set(schema) - _KEYWORDS:
        raise ValueError("bundled schema contains an unsupported keyword")
    kind = schema.get("type")
    correct = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": type(value) is int,
        "boolean": type(value) is bool,
    }
    if kind not in correct or not correct[kind]:
        raise ValueError(f"{location} must be {kind}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{location} has an unsupported value")
    if kind == "object":
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{location} is missing required fields")
        fields = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(fields):
            raise ValueError(f"{location} has unknown fields")
        for name, item in value.items():
            if name in fields:
                _check(item, fields[name], f"{location}.{name}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            raise ValueError(f"{location} has an invalid number of entries")
        for index, item in enumerate(value):
            _check(item, schema["items"], f"{location}[{index}]")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
            raise ValueError(f"{location} has an invalid text length")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{location} has an invalid text format")


def validate_structure(payload: Any) -> None:
    _check(payload, json.loads(SCHEMA_PATH.read_text(encoding="utf-8")), "payload")
