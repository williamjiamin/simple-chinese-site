"""Original, bounded pinyin display rules for explicitly annotated syllables.

This module is a spelling/display normalizer, not a pronunciation dictionary.
Callers supply lexical pronunciations and prosodic boundaries. Third-tone runs
use a deterministic adjacent-pair teaching policy: every non-final third tone
in a supplied group is displayed as second tone. Natural speech can require
different grouping; insert ``boundary_after=True`` wherever the rule must stop.
No half-third-tone, regional, lexical-neutral, or polyphonic inference is made.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import re
import unicodedata


_TONE_TO_MARK = {1: "\u0304", 2: "\u0301", 3: "\u030c", 4: "\u0300"}
_MARK_TO_TONE = {value: key for key, value in _TONE_TO_MARK.items()}
_VOWELS = frozenset("aeiouüAEIOUÜ")
_LETTERS = re.compile(r"[A-Za-züÜ]+\Z")


@dataclass(frozen=True)
class Syllable:
    """One author-supplied lexical syllable and its explicit speech context.

    ``neutral`` suppresses the spoken tone while retaining the citation form.
    A known citation tone still supplies the following-tone context for 一/不.
    ``ordinal`` protects the lexical first tone of an ordinal ``一``.
    """

    hanzi: str
    pinyin: str
    boundary_after: bool = False
    ordinal: bool = False
    neutral: bool = False


@dataclass(frozen=True)
class _Parsed:
    spelling: str
    tone: int


def normalize_utf8(data: bytes) -> bytes:
    """Strictly decode UTF-8, apply NFC, and re-encode without newline changes.

    A leading byte-order mark is retained. Invalid byte sequences and lone
    surrogate encodings raise ``UnicodeDecodeError`` instead of being replaced.
    This operation only normalizes Unicode; it does not interpret tone digits.
    """

    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    return unicodedata.normalize("NFC", data.decode("utf-8", errors="strict")).encode(
        "utf-8", errors="strict"
    )


def _tone_position(spelling: str) -> int:
    lower = spelling.lower()
    for priority in ("a", "e"):
        if priority in lower:
            return lower.index(priority)
    if "ou" in lower:
        return lower.index("ou")
    positions = [i for i, char in enumerate(spelling) if char in _VOWELS]
    if positions:
        return positions[-1]
    # Syllabic nasals are supported without a vocabulary lookup.
    if lower in {"m", "n", "ng"}:
        return 0
    raise ValueError("syllable must contain a vowel or be a syllabic nasal")


def _format(spelling: str, tone: int) -> str:
    if tone == 5:
        return unicodedata.normalize("NFC", spelling)
    position = _tone_position(spelling)
    return unicodedata.normalize(
        "NFC", spelling[: position + 1] + _TONE_TO_MARK[tone] + spelling[position + 1 :]
    )


def _parse(text: str) -> _Parsed:
    if not isinstance(text, str):
        raise TypeError("pinyin must be a string")
    if not text or text != text.strip():
        raise ValueError("supply one non-empty syllable without surrounding whitespace")

    # Preserve case while unifying the two keyboard spellings of ü.
    candidate = text.replace("u:", "ü").replace("U:", "Ü")
    candidate = candidate.replace("v", "ü").replace("V", "Ü")
    numeric_tone: int | None = None
    if candidate[-1:] in "012345" and candidate[-1:]:
        numeric_tone = int(candidate[-1])
        numeric_tone = 5 if numeric_tone == 0 else numeric_tone
        candidate = candidate[:-1]
    if not candidate:
        raise ValueError("missing syllable before tone digit")

    chars: list[str] = []
    marked_tone: int | None = None
    marked_position: int | None = None
    for char in unicodedata.normalize("NFD", candidate):
        if char in _MARK_TO_TONE:
            if not chars or marked_tone is not None:
                raise ValueError("at most one tone mark is allowed per syllable")
            marked_tone = _MARK_TO_TONE[char]
            marked_position = len(chars) - 1
        elif char == "\u0308":
            if not chars or chars[-1] not in {"u", "U"}:
                raise ValueError("diaeresis is only supported once on u")
            chars[-1] = "ü" if chars[-1] == "u" else "Ü"
        elif unicodedata.combining(char):
            raise ValueError("unsupported combining mark in syllable")
        else:
            chars.append(char)

    spelling = "".join(chars)
    if not _LETTERS.fullmatch(spelling):
        raise ValueError("supply one syllable with letters and an optional final tone digit")
    position = _tone_position(spelling)
    if numeric_tone is not None and marked_tone is not None:
        raise ValueError("do not combine tone digits and tone marks")
    if marked_tone is not None and marked_position != position:
        raise ValueError("tone mark is on the wrong letter")
    return _Parsed(spelling, numeric_tone or marked_tone or 5)


def mark_syllable(text: str) -> str:
    """Return canonical NFC tone-mark spelling for one annotated syllable.

    Digits 1–4 are marked; 0 and 5 mean neutral. Unmarked text is neutral.
    ``v`` and ``u:`` denote ü. Uppercase is preserved. Already marked input is
    accepted when its mark is correctly placed. Mixed marked/numbered input,
    more than one tone mark, invalid digits, and misplaced marks are rejected.
    Spelling is structurally checked, not validated against a word dictionary.
    """

    parsed = _parse(text)
    return _format(parsed.spelling, parsed.tone)


def render_phrase(
    tokens: Sequence[Syllable], *, enabled: bool | None = None
) -> dict[str, object]:
    """Produce separate citation and spoken forms from immutable annotations.

    The ON path uses lexical next tones for 一/不 and third-tone pairs, so a
    prior change never cascades into a later rule. A boundary on the current
    token stops the rule from crossing to the following token. The OFF path is a literal passthrough
    of supplied pinyin, even when it is not valid normalized pinyin.

    The feature flag is read at call time and its environment kill switch takes
    precedence over explicit True. False bypasses normalization and all rules.
    The function never modifies
    supplied annotations and does not perform I/O or vocabulary lookup.
    """

    from .flags import is_enabled

    active = is_enabled(enabled)

    if not active:
        return {
            "enabled": False,
            "citation": " ".join(token.pinyin for token in tokens),
            "spoken": " ".join(token.pinyin for token in tokens),
            "tokens": [
                {
                    "hanzi": token.hanzi,
                    "input": token.pinyin,
                    "citation": token.pinyin,
                    "spoken": token.pinyin,
                    "citation_tone": None,
                    "spoken_tone": None,
                    "rule": "disabled",
                }
                for token in tokens
            ],
        }

    if isinstance(tokens, (str, bytes)) or not isinstance(tokens, Sequence):
        raise TypeError("tokens must be a sequence of Syllable values")
    for token in tokens:
        if not isinstance(token, Syllable):
            raise TypeError("tokens must contain only Syllable values")
        if not isinstance(token.hanzi, str) or not isinstance(token.pinyin, str):
            raise TypeError("hanzi and pinyin must be strings")
        if any(
            not isinstance(value, bool)
            for value in (token.boundary_after, token.ordinal, token.neutral)
        ):
            raise TypeError("syllable context fields must be booleans")

    parsed = [_parse(token.pinyin) for token in tokens]
    lexical_tones = [item.tone for item in parsed]
    rows: list[dict[str, object]] = []
    for index, (token, item) in enumerate(zip(tokens, parsed)):
        spoken_tone = 5 if token.neutral else item.tone
        rule = "explicit-neutral" if token.neutral else "citation"
        next_tone = (
            lexical_tones[index + 1]
            if index + 1 < len(tokens) and not token.boundary_after
            else None
        )
        if not token.neutral:
            if token.hanzi == "一" and item.spelling.lower() == "yi" and item.tone == 1:
                if not token.ordinal and next_tone == 4:
                    spoken_tone, rule = 2, "one-before-fourth"
                elif not token.ordinal and next_tone in {1, 2, 3}:
                    spoken_tone, rule = 4, "one-before-non-fourth"
            elif token.hanzi == "不" and item.spelling.lower() == "bu" and item.tone == 4:
                if next_tone == 4:
                    spoken_tone, rule = 2, "not-before-fourth"
            elif item.tone == 3 and next_tone == 3 and not tokens[index + 1].neutral:
                spoken_tone, rule = 2, "third-before-third"
        rows.append(
            {
                "hanzi": token.hanzi,
                "input": token.pinyin,
                "citation": _format(item.spelling, item.tone),
                "spoken": _format(item.spelling, spoken_tone),
                "citation_tone": item.tone,
                "spoken_tone": spoken_tone,
                "rule": rule,
            }
        )
    return {
        "enabled": True,
        "citation": " ".join(str(row["citation"]) for row in rows),
        "spoken": " ".join(str(row["spoken"]) for row in rows),
        "tokens": rows,
    }
