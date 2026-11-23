"""Escaped, bounded HTML insertion with an exact-byte rollback path."""

from __future__ import annotations

import html
import json
import unicodedata
from typing import Any

from .data_schema import validate_structure
from .flags import is_enabled
from .pinyin import Syllable, mark_syllable, normalize_utf8, render_phrase

MARKER = b"<!-- CHINESE_SITE_GUARD -->"
MAX_DATA_BYTES = 1_048_576
MAX_SOURCE_BYTES = 8_388_608


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_payload(data: bytes) -> dict[str, Any]:
    """Decode strict UTF-8 JSON, reject ambiguous keys and non-finite numbers."""
    if len(data) > MAX_DATA_BYTES:
        raise ValueError("phrase file exceeds 1 MiB")
    value = json.loads(
        data.decode("utf-8-sig", errors="strict"),
        object_pairs_hook=_no_duplicates,
        parse_constant=lambda text: (_ for _ in ()).throw(ValueError(f"invalid number: {text}")),
    )
    validate_payload(value)
    return value


def _text(value: str, label: str) -> str:
    if not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    value.encode("utf-8", errors="strict")
    if any(unicodedata.category(c) in {"Cc", "Cs"} or c == "\ufffd" for c in value):
        raise ValueError(f"{label} contains a forbidden control or replacement character")
    return value


def validate_payload(payload: Any) -> None:
    """Apply the bundled schema plus text, identity and tone semantics."""
    validate_structure(payload)
    phrases = payload["phrases"]
    identifiers: set[str] = set()
    for phrase in phrases:
        identifier = _text(phrase["id"], "id")
        if identifier in identifiers:
            raise ValueError("phrase ids must be unique lower-case identifiers")
        identifiers.add(identifier)
        _text(phrase["translation"], "translation")
        tokens = phrase["tokens"]
        for token in tokens:
            _text(token["hanzi"], "hanzi")
            mark_syllable(_text(token["pinyin"], "pinyin"))


def normalize_bytes(source: bytes, *, enabled: bool | None = None) -> bytes:
    """OFF returns the very same byte object before any decoding or validation."""
    if not is_enabled(enabled):
        return source
    return normalize_utf8(source)


def _escape(value: str) -> str:
    return html.escape(unicodedata.normalize("NFC", value), quote=True)


def render_html(source: bytes, payload: Any, *, enabled: bool | None = None) -> bytes:
    """Insert at one explicit marker; leave every surrounding source byte intact.

    The display script only switches precomputed strings. This module remains
    the single source for linguistic behavior. The caller serves display.js
    alongside the resulting page when using its optional mode button.
    """
    if not is_enabled(enabled):
        return source
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("HTML input exceeds 8 MiB")
    source.decode("utf-8-sig", errors="strict")
    if source.count(MARKER) != 1:
        raise ValueError("HTML requires exactly one CHINESE_SITE_GUARD marker")
    validate_payload(payload)
    blocks = [
        '<section id="tone-study" lang="en" aria-labelledby="tone-study-title">',
        '<h2 id="tone-study-title">Pinyin in context</h2>',
        '<p>Spoken tones are shown by default. Dictionary spellings stay visible for comparison.</p>',
        '<button type="button" id="tone-mode" aria-pressed="true" aria-controls="tone-examples">Spoken tones</button>',
        '<div id="tone-examples" aria-live="polite">',
    ]
    for phrase in payload["phrases"]:
        result = render_phrase([Syllable(**token) for token in phrase["tokens"]], enabled=True)
        hanzi = "".join(token["hanzi"] for token in phrase["tokens"])
        blocks.extend([
            f'<article class="tone-card" aria-labelledby="phrase-{phrase["id"]}">',
            f'<h3 lang="zh-Hans" id="phrase-{phrase["id"]}">{_escape(hanzi)}</h3>',
            f'<p class="pinyin" lang="zh-Latn-pinyin" data-citation="{_escape(result["citation"])}" data-spoken="{_escape(result["spoken"])}">{_escape(result["spoken"])}</p>',
            f'<p class="citation">Dictionary: <span lang="zh-Latn-pinyin">{_escape(result["citation"])}</span></p>',
            f'<p>{_escape(phrase["translation"])}</p>',
            '</article>',
        ])
    blocks.extend([
        '</div>',
        '<p class="scope-note">This is a bounded learning display. Word boundaries, neutral readings and ordinal uses are supplied by the author.</p>',
        '</section>',
        '<script src="display.js" defer></script>',
    ])
    return source.replace(MARKER, "\n".join(blocks).encode("utf-8"), 1)
