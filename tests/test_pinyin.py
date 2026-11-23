"""Original examples and boundary tests for the annotated pinyin display core.

SPDX-License-Identifier: MIT
"""

from dataclasses import FrozenInstanceError
import json
import unicodedata

import pytest

from tools.chinese_site_guard.pinyin import Syllable, mark_syllable, normalize_utf8, render_phrase


@pytest.fixture(autouse=True)
def clean_flag_environment(monkeypatch):
    monkeypatch.delenv("CHINESE_SITE_GUARD_DISABLED", raising=False)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("ma1", "mā"), ("ma2", "má"), ("ma3", "mǎ"), ("ma4", "mà"),
        ("ma0", "ma"), ("ma5", "ma"), ("ma", "ma"),
        ("xian1", "xiān"), ("shui3", "shuǐ"), ("liu2", "liú"),
        ("gui4", "guì"), ("xiao3", "xiǎo"), ("hou4", "hòu"),
        ("dou1", "dōu"), ("gei3", "gěi"), ("nue4", "nuè"),
        ("lü4", "lǜ"), ("lv4", "lǜ"), ("lu:4", "lǜ"),
        ("nu:3", "nǚ"), ("nv3", "nǚ"), ("nü3", "nǚ"),
        ("lve4", "lüè"), ("nüe4", "nüè"),
        ("LU:4", "LǛ"), ("NÜ3", "NǙ"), ("SHUI3", "SHUǏ"),
        ("Ai4", "Ài"), ("OU3", "ǑU"),
        ("n2", "ń"), ("n3", "ň"), ("ng4", "ǹg"), ("m2", "ḿ"),
        ("m1", "m\u0304"), ("n5", "n"),
        ("n\u01d0", "nǐ"), ("nu\u0308\u030c", "nǚ"),
        ("nu\u030c\u0308", "nǚ"), ("xie\u0300", "xiè"),
        ("nü", "nü"), ("nv", "nü"), ("nu:", "nü"),
    ],
)
def test_mark_syllable(source, expected):
    actual = mark_syllable(source)
    assert actual == expected
    assert unicodedata.is_normalized("NFC", actual)
    assert mark_syllable(actual) == actual


@pytest.mark.parametrize(
    "source",
    [
        "", " ni3", "ni3 ", "ni3 hao3", "ni6", "ni9", "ni33", "3", "6",
        "nǐ3", "nǐ0", "nǐ4", "nǐǎ", "n\u030ci3", "ńi", "nî", "a\u0308",
        "u\u0308\u0308", "u\u0301\u0301", "n i", "你好", "ni-hao", "xíao",
        "lv:", "l:u4", "ha!", "bbb3", "\u0301a", "n\ud800i", "ā̀", "ouǐ",
    ],
)
def test_invalid_syllables_fail_closed(source):
    with pytest.raises(ValueError):
        mark_syllable(source)


@pytest.mark.parametrize("source", [None, 3, b"ni3", ["ni3"]])
def test_non_string_syllables_are_rejected(source):
    with pytest.raises(TypeError):
        mark_syllable(source)


def test_utf8_nfc_retains_hanzi_bom_newlines_and_astral_characters():
    source = "\ufeff汉字\r\nnu\u0308\u030c \U00020000\n\n".encode("utf-8")
    expected = "\ufeff汉字\r\nnǚ \U00020000\n\n".encode("utf-8")
    result = normalize_utf8(source)
    assert result == expected
    assert normalize_utf8(result) == result
    assert b"\r\n" in result
    assert result.startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("invalid", [b"\xff", b"\xc0\xaf", b"\xe4\xb8", b"\xed\xa0\x80"])
def test_invalid_utf8_is_never_silently_replaced(invalid):
    with pytest.raises(UnicodeDecodeError):
        normalize_utf8(invalid)


@pytest.mark.parametrize("invalid", ["汉字", None, bytearray(b"ma3")])
def test_normalize_requires_bytes(invalid):
    with pytest.raises(TypeError):
        normalize_utf8(invalid)


def test_unicode_normalization_does_not_interpret_digits_or_strip_characters():
    source = "mi3\t米\r\n  雨\n".encode()
    assert normalize_utf8(source) == source
    assert normalize_utf8(b"") == b""


def test_third_tone_pair_retains_citation_form():
    result = render_phrase([Syllable("你", "ni3"), Syllable("好", "hao3")], enabled=True)
    assert result["citation"] == "nǐ hǎo"
    assert result["spoken"] == "ní hǎo"
    assert result["tokens"][0]["rule"] == "third-before-third"
    assert result["tokens"][0]["citation_tone"] == 3
    assert result["tokens"][0]["spoken_tone"] == 2


@pytest.mark.parametrize(
    ("next_pinyin", "next_hanzi", "expected"),
    [("tian1", "天", "yì tiān"), ("nian2", "年", "yì nián"),
     ("ben3", "本", "yì běn"), ("ban4", "半", "yí bàn"),
     ("ge5", "个", "yī ge")],
)
def test_one_rules_use_next_lexical_tone(next_pinyin, next_hanzi, expected):
    result = render_phrase([Syllable("一", "yi1"), Syllable(next_hanzi, next_pinyin)], enabled=True)
    assert result["spoken"] == expected
    assert result["citation"].startswith("yī ")


@pytest.mark.parametrize(
    ("next_pinyin", "expected"),
    [("hao3", "bù hǎo"), ("lai2", "bù lái"), ("chi1", "bù chī"),
     ("shi4", "bú shì"), ("le5", "bù le")],
)
def test_not_changes_only_before_fourth(next_pinyin, expected):
    assert render_phrase([Syllable("不", "bu4"), Syllable("字", next_pinyin)], enabled=True)["spoken"] == expected


def test_third_tone_runs_follow_explicit_adjacent_pair_policy():
    tokens = [Syllable("雨", "yu3"), Syllable("伞", "san3"), Syllable("好", "hao3")]
    assert render_phrase(tokens, enabled=True)["spoken"] == "yú sán hǎo"
    grouped = [Syllable("雨", "yu3", boundary_after=True), *tokens[1:]]
    assert render_phrase(grouped, enabled=True)["spoken"] == "yǔ sán hǎo"


@pytest.mark.parametrize("first,second,expected", [
    (Syllable("你", "ni3", boundary_after=True), Syllable("好", "hao3"), "nǐ hǎo"),
    (Syllable("一", "yi1", boundary_after=True), Syllable("半", "ban4"), "yī bàn"),
    (Syllable("不", "bu4", boundary_after=True), Syllable("是", "shi4"), "bù shì"),
    (Syllable("一", "yi1", ordinal=True), Syllable("课", "ke4"), "yī kè"),
])
def test_boundaries_and_ordinal_protection(first, second, expected):
    assert render_phrase([first, second], enabled=True)["spoken"] == expected


def test_rules_never_cascade_from_newly_assigned_surface_tones():
    result = render_phrase([Syllable("一", "yi1"), Syllable("不", "bu4"), Syllable("是", "shi4")], enabled=True)
    assert result["citation"] == "yī bù shì"
    assert result["spoken"] == "yí bú shì"


def test_explicit_neutral_retains_lexical_context_but_blocks_third_tone_pairs():
    result = render_phrase([Syllable("一", "yi1"), Syllable("个", "ge4", neutral=True)], enabled=True)
    assert result["citation"] == "yī gè"
    assert result["spoken"] == "yí ge"
    assert result["tokens"][1]["rule"] == "explicit-neutral"
    third = render_phrase([Syllable("好", "hao3"), Syllable("好", "hao3", neutral=True)], enabled=True)
    assert third["spoken"] == "hǎo hao"
    fourth = render_phrase([Syllable("不", "bu4"), Syllable("是", "shi4", neutral=True)], enabled=True)
    assert fourth["spoken"] == "bú shi"


def test_unknown_underlying_neutral_tone_is_not_invented():
    result = render_phrase([Syllable("一", "yi1"), Syllable("个", "ge5")], enabled=True)
    assert result["spoken"] == "yī ge"


def test_identical_romanization_without_correct_hanzi_does_not_trigger_lexical_rules():
    result = render_phrase([Syllable("衣", "yi1"), Syllable("半", "ban4")], enabled=True)
    assert result["spoken"] == "yī bàn"
    result = render_phrase([Syllable("布", "bu4"), Syllable("料", "liao4")], enabled=True)
    assert result["spoken"] == "bù liào"


def test_inconsistent_lexical_spelling_does_not_trigger_one_rule():
    result = render_phrase([Syllable("一", "ya1"), Syllable("半", "ban4")], enabled=True)
    assert result["spoken"] == "yā bàn"


def test_no_context_at_phrase_end_and_empty_phrase():
    assert render_phrase([Syllable("一", "yi1")], enabled=True)["spoken"] == "yī"
    assert render_phrase([Syllable("不", "bu4")], enabled=True)["spoken"] == "bù"
    assert render_phrase([], enabled=True) == {"enabled": True, "citation": "", "spoken": "", "tokens": []}


def test_inputs_are_immutable_and_result_is_json_serializable():
    token = Syllable("女", "nu:3")
    tokens = [token]
    result = render_phrase(tokens, enabled=True)
    assert token.pinyin == "nu:3" and tokens == [token]
    assert json.loads(json.dumps(result, ensure_ascii=False))["spoken"] == "nǚ"
    with pytest.raises(FrozenInstanceError):
        token.pinyin = "nv3"


def test_off_is_literal_passthrough_including_decomposed_or_invalid_legacy_pinyin():
    tokens = [Syllable("你", "ni3"), Syllable("旧", "legacy < untouched\r\n"), Syllable("女", "nu\u0308\u030c")]
    result = render_phrase(tokens, enabled=False)
    expected = " ".join(token.pinyin for token in tokens)
    assert result["enabled"] is False
    assert result["citation"].encode() == expected.encode()
    assert result["spoken"].encode() == expected.encode()
    assert all(row["rule"] == "disabled" for row in result["tokens"])


def test_environment_flag_is_read_on_each_call(monkeypatch):
    from tools.chinese_site_guard import flags

    tokens = [Syllable("女", "nu:3")]
    monkeypatch.setattr(flags, "is_enabled", lambda requested=None: False)
    assert render_phrase(tokens)["spoken"] == "nu:3"
    monkeypatch.setattr(flags, "is_enabled", lambda requested=None: True)
    assert render_phrase(tokens)["spoken"] == "nǚ"


def test_environment_kill_switch_overrides_explicit_enable(monkeypatch):
    monkeypatch.setenv("CHINESE_SITE_GUARD_DISABLED", "true")
    assert render_phrase([Syllable("女", "nu:3")], enabled=True)["spoken"] == "nu:3"


def test_off_does_not_validate_context_or_normalize_old_annotations():
    result = render_phrase([Syllable("旧", "legacy 3 4", boundary_after="legacy-value")], enabled=False)
    assert result["spoken"] == "legacy 3 4"


@pytest.mark.parametrize("tokens", ["ni3", b"ni3", None, ["ni3"], [{"pinyin": "ni3"}]])
def test_invalid_token_containers(tokens):
    with pytest.raises(TypeError):
        render_phrase(tokens, enabled=True)


@pytest.mark.parametrize("token", [Syllable(None, "ni3"), Syllable("你", 3), Syllable("你", "ni3", boundary_after=1)])
def test_invalid_token_field_types(token):
    with pytest.raises(TypeError):
        render_phrase([token], enabled=True)


@pytest.mark.parametrize("enabled", [0, 1, "false", [], {}])
def test_non_boolean_enabled_is_rejected(enabled):
    with pytest.raises(TypeError):
        render_phrase([], enabled=enabled)


def test_long_phrase_is_deterministic_without_input_mutation():
    tokens = [Syllable("好", "hao3") for _ in range(2000)]
    result = render_phrase(tokens, enabled=True)
    assert result["spoken"].split() == ["háo"] * 1999 + ["hǎo"]
    assert all(token.pinyin == "hao3" for token in tokens)
