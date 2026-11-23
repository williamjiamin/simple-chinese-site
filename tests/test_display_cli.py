"""Independent boundary, rollback, injection and command-line checks.

SPDX-License-Identifier: MIT
"""

from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.chinese_site_guard.display import (
    MARKER,
    MAX_DATA_BYTES,
    MAX_SOURCE_BYTES,
    load_payload,
    normalize_bytes,
    render_html,
    validate_payload,
)
from tools.chinese_site_guard.flags import is_enabled


_ROOT = Path(__file__).resolve().parents[1]
_FLAG = "CHINESE_SITE_GUARD_DISABLED"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv(_FLAG, raising=False)


@pytest.fixture
def payload():
    return {
        "schema_version": 1,
        "phrases": [{
            "id": "greeting",
            "translation": "A greeting.",
            "tokens": [{"hanzi": "你", "pinyin": "ni3"}, {"hanzi": "好", "pinyin": "hao3"}],
        }],
    }


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=True).encode("utf-8")


def _run(*arguments, env=None):
    environment = dict(os.environ)
    environment.pop(_FLAG, None)
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", "tools.chinese_site_guard", *map(str, arguments)],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _status(process):
    return json.loads(process.stdout or process.stderr)


def _site(root):
    root.mkdir()
    for name, title, content, canonical in [
        ("index.html", "Study", "Read this local page.", "https://study.invalid/"),
        ("privacy.html", "Privacy", "This example makes no remote requests.", "https://study.invalid/privacy.html"),
    ]:
        (root / name).write_text(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{title}</title><link rel="canonical" href="{canonical}"></head>'
            '<body><a href="#main">Skip to content</a><main id="main">'
            f'<h1>{title}</h1><p>{content}</p><a href="privacy.html">Privacy</a>'
            '</main></body></html>',
            encoding="utf-8",
        )
    return root


def test_feature_ships_enabled_and_explicit_false_bypasses():
    assert is_enabled() is True
    assert is_enabled(True) is True
    assert is_enabled(False) is False
    assert normalize_bytes("nu\u0308\u030c".encode()) == "nǚ".encode()


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", " Yes ", "ON"])
def test_kill_values_override_explicit_enable(monkeypatch, value):
    monkeypatch.setenv(_FLAG, value)
    assert is_enabled() is False
    assert is_enabled(True) is False
    source = b"\xff\xed\xa0\x80\r\n"
    assert normalize_bytes(source, enabled=True) is source
    assert render_html(source, object(), enabled=True) is source


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", " FALSE ", "OFF"])
def test_non_kill_values_leave_default_on(monkeypatch, value):
    monkeypatch.setenv(_FLAG, value)
    assert is_enabled() is True
    assert is_enabled(False) is False


@pytest.mark.parametrize("value", ["maybe", "2", "disabled", "truthy"])
def test_invalid_flag_values_report_configuration_error(monkeypatch, value):
    monkeypatch.setenv(_FLAG, value)
    with pytest.raises(ValueError):
        is_enabled()


@pytest.mark.parametrize("requested", [0, 1, "true", "false", [], {}])
def test_flag_rejects_non_boolean_override(requested):
    with pytest.raises(TypeError):
        is_enabled(requested)


def test_normalize_off_is_identical_object_before_decode():
    source = b"\xff\xfe\x00invalid\r\n"
    assert normalize_bytes(source, enabled=False) is source
    with pytest.raises(UnicodeDecodeError):
        normalize_bytes(source, enabled=True)


def test_normalize_preserves_bom_crlf_and_does_not_replace_hanzi():
    source = "\ufeff汉字\r\nnu\u0308\u030c\n".encode()
    assert normalize_bytes(source) == "\ufeff汉字\r\nnǚ\n".encode()
    assert normalize_bytes(source, enabled=False) is source


def test_render_off_does_not_inspect_marker_payload_size_or_encoding():
    source = b"\xff" + b"x" * (MAX_SOURCE_BYTES + 1)
    assert render_html(source, {"invalid": object()}, enabled=False) is source


def test_render_on_replaces_only_the_opt_in_marker(payload):
    prefix = "\ufeff<!doctype html>\r\n<main>outside nu\u0308\u030c\r\n".encode()
    suffix = b"\r\n</main>\r\n<!-- original tail -->"
    source = prefix + MARKER + suffix
    output = render_html(source, payload)
    assert output.startswith(prefix)
    assert output.endswith(suffix)
    assert output.count(b'id="tone-study"') == 1
    assert MARKER not in output
    assert source == prefix + MARKER + suffix
    assert 'data-citation="nǐ hǎo"'.encode() in output
    assert 'data-spoken="ní hǎo"'.encode() in output
    assert b'aria-pressed="true"' in output
    assert b'lang="zh-Hans"' in output and b'lang="zh-Latn-pinyin"' in output


@pytest.mark.parametrize("source", [b"<main></main>", MARKER + MARKER])
def test_marker_must_occur_exactly_once(source, payload):
    with pytest.raises(ValueError):
        render_html(source, payload)


def test_renderer_rejects_invalid_utf8_on_path(payload):
    with pytest.raises(UnicodeDecodeError):
        render_html(b"\xff" + MARKER, payload)


def test_source_size_limit_is_enforced_before_generation(payload):
    exact = MARKER + b" " * (MAX_SOURCE_BYTES - len(MARKER))
    assert render_html(exact, payload).endswith(b" " * 100)
    with pytest.raises(ValueError):
        render_html(exact + b" ", payload)


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_untrusted_display_strings_cannot_create_html(payload):
    payload["phrases"][0]["translation"] = '</p><script>alert("x")</script><img src=x onerror=1>'
    payload["phrases"][0]["tokens"][0]["hanzi"] = '<img src=x>'
    output = render_html(MARKER, payload).decode()
    parser = _Tags()
    parser.feed(output)
    assert not any(tag == "img" for tag, _ in parser.tags)
    scripts = [attrs for tag, attrs in parser.tags if tag == "script"]
    assert scripts == [{"src": "display.js", "defer": None}]
    assert "&lt;script&gt;" in output
    assert "&lt;img src=x&gt;" in output
    assert not any("onerror" in attrs for _, attrs in parser.tags)


def test_injection_through_identifier_is_rejected(payload):
    payload["phrases"][0]["id"] = 'x" onclick="run'
    with pytest.raises(ValueError):
        render_html(MARKER, payload)


def test_valid_json_accepts_bom_and_keeps_input_data_unchanged(payload):
    original = deepcopy(payload)
    data = b"\xef\xbb\xbf" + _json_bytes(payload)
    assert load_payload(data) == original
    render_html(MARKER, payload)
    assert payload == original


@pytest.mark.parametrize("data", [
    b'{"schema_version":1,"schema_version":1,"phrases":[]}',
    b'{"schema_version":1,"phrases":[{"id":"a","id":"b"}]}',
    b'{"schema_version":NaN,"phrases":[]}',
    b'{"schema_version":Infinity,"phrases":[]}',
    b'{"schema_version":-Infinity,"phrases":[]}',
    b'{"schema_version":1e999,"phrases":[]}',
    b'{"schema_version":1,"phrases":[],}',
    b"\xff",
    b"null",
    b"[]",
])
def test_ambiguous_or_malformed_json_is_rejected(data):
    with pytest.raises((ValueError, UnicodeError)):
        load_payload(data)


@pytest.mark.parametrize("field,value", [
    ("translation", ""), ("translation", "   "), ("translation", "\ud800"),
    ("translation", "text\x00"), ("translation", "text\ufffd"),
    ("translation", "text\nline"), ("translation", 3), ("id", ""),
    ("id", "Upper"), ("id", "bad/id"), ("id", "id\n"),
])
def test_payload_rejects_invalid_phrase_text(payload, field, value):
    payload["phrases"][0][field] = value
    with pytest.raises((ValueError, UnicodeError)):
        load_payload(_json_bytes(payload))


@pytest.mark.parametrize("field,value", [
    ("hanzi", ""), ("hanzi", "\udfff"), ("hanzi", "\x01"),
    ("pinyin", ""), ("pinyin", "nǐ3"), ("pinyin", "ni7"), ("pinyin", "\ud800"),
    ("neutral", "true"), ("ordinal", 1), ("boundary_after", None),
])
def test_payload_rejects_invalid_token_fields(payload, field, value):
    payload["phrases"][0]["tokens"][0][field] = value
    with pytest.raises((ValueError, UnicodeError)):
        load_payload(_json_bytes(payload))


def test_data_validation_remains_strict_when_display_is_disabled(payload, monkeypatch):
    monkeypatch.setenv(_FLAG, "true")
    payload["phrases"][0]["tokens"][0]["pinyin"] = "ni7"
    with pytest.raises(ValueError):
        load_payload(_json_bytes(payload))


@pytest.mark.parametrize("level", ["payload", "phrase", "token"])
def test_unknown_fields_are_not_silently_accepted(payload, level):
    target = payload if level == "payload" else payload["phrases"][0]
    if level == "token":
        target = target["tokens"][0]
    target["unknown"] = True
    with pytest.raises(ValueError):
        validate_payload(payload)


@pytest.mark.parametrize("version", [True, False, 0, 2, 1.0, "1", None])
def test_schema_version_requires_exact_integer_one(payload, version):
    payload["schema_version"] = version
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_required_fields_and_unique_ids(payload):
    for path in [(), ("phrases", 0), ("phrases", 0, "tokens", 0)]:
        broken = deepcopy(payload)
        value = broken
        for key in path:
            value = value[key]
        del value[next(iter(value))]
        with pytest.raises(ValueError):
            validate_payload(broken)
    payload["phrases"].append(deepcopy(payload["phrases"][0]))
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_phrase_and_token_count_boundaries(payload):
    phrase = deepcopy(payload["phrases"][0])
    payload["phrases"] = [dict(deepcopy(phrase), id=f"item-{i}") for i in range(256)]
    validate_payload(payload)
    payload["phrases"].append(dict(deepcopy(phrase), id="extra"))
    with pytest.raises(ValueError):
        validate_payload(payload)
    payload["phrases"] = []
    with pytest.raises(ValueError):
        validate_payload(payload)
    payload["phrases"] = [phrase]
    phrase["tokens"] = [{"hanzi": "好", "pinyin": "hao3"} for _ in range(128)]
    validate_payload(payload)
    phrase["tokens"].append({"hanzi": "好", "pinyin": "hao3"})
    with pytest.raises(ValueError):
        validate_payload(payload)
    phrase["tokens"] = []
    with pytest.raises(ValueError):
        validate_payload(payload)


@pytest.mark.parametrize("field,bound,is_token", [
    ("id", 64, False), ("translation", 512, False), ("hanzi", 16, True), ("pinyin", 32, True),
])
def test_text_length_boundaries(payload, field, bound, is_token):
    target = payload["phrases"][0]
    if is_token:
        target = target["tokens"][0]
    target[field] = "a" * bound
    validate_payload(payload)
    target[field] += "a"
    with pytest.raises(ValueError):
        validate_payload(payload)


def test_json_byte_limit(payload):
    data = _json_bytes(payload)
    exact = data + b" " * (MAX_DATA_BYTES - len(data))
    assert load_payload(exact) == payload
    with pytest.raises(ValueError):
        load_payload(exact + b" ")


def test_cli_normalize_emits_digest_json_and_preserves_source(tmp_path):
    source = tmp_path / "source.txt"
    destination = tmp_path / "normalized.txt"
    original = "\ufeff汉字\r\nnu\u0308\u030c\n".encode()
    source.write_bytes(original)
    process = _run("normalize", source, "--output", destination)
    assert process.returncode == 0 and not process.stderr
    result = _status(process)
    expected = "\ufeff汉字\r\nnǚ\n".encode()
    assert destination.read_bytes() == expected and source.read_bytes() == original
    assert result == {"ok": True, "enabled": True, "changed": True,
                      "bytes": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}


def test_cli_rejects_invalid_utf8_without_creating_output(tmp_path):
    source, destination = tmp_path / "invalid.txt", tmp_path / "out.txt"
    source.write_bytes(b"\xff")
    process = _run("normalize", source, "--output", destination)
    assert process.returncode == 2 and not process.stdout
    assert _status(process)["ok"] is False
    assert not destination.exists() and source.read_bytes() == b"\xff"


def test_cli_off_normalize_preserves_invalid_bytes(tmp_path):
    source, destination = tmp_path / "invalid.txt", tmp_path / "out.txt"
    source.write_bytes(b"\xff\r\n\x00")
    process = _run("normalize", source, "--output", destination, env={_FLAG: "true"})
    assert process.returncode == 0
    assert destination.read_bytes() == source.read_bytes()
    assert _status(process)["changed"] is False and _status(process)["enabled"] is False


def test_cli_off_render_ignores_missing_phrase_file_and_invalid_source(tmp_path):
    source, destination = tmp_path / "legacy.html", tmp_path / "out.html"
    source.write_bytes(b"\xfflegacy without marker\r\n")
    process = _run("render", source, "--phrases", tmp_path / "absent.json", "--output", destination,
                   env={_FLAG: "1"})
    assert process.returncode == 0
    assert destination.read_bytes() == source.read_bytes()
    assert _status(process)["changed"] is False


def test_cli_render_compiles_spoken_labels_and_keeps_source(payload, tmp_path):
    source, destination, phrases = tmp_path / "source.html", tmp_path / "out.html", tmp_path / "phrases.json"
    source.write_bytes(b"<main>" + MARKER + b"</main>")
    phrases.write_bytes(_json_bytes(payload))
    before = source.read_bytes()
    process = _run("render", source, "--phrases", phrases, "--output", destination)
    assert process.returncode == 0 and not process.stderr
    assert 'data-spoken="ní hǎo"'.encode() in destination.read_bytes()
    assert source.read_bytes() == before
    assert _status(process)["changed"] is True


def test_cli_invalid_phrases_fail_without_writing(payload, tmp_path):
    source, destination, phrases = tmp_path / "source.html", tmp_path / "out.html", tmp_path / "phrases.json"
    source.write_bytes(MARKER)
    payload["phrases"][0]["tokens"][0]["pinyin"] = "ni6"
    phrases.write_bytes(_json_bytes(payload))
    process = _run("render", source, "--phrases", phrases, "--output", destination)
    assert process.returncode == 2 and not destination.exists()
    assert _status(process)["ok"] is False


@pytest.mark.parametrize("force", [False, True])
def test_cli_never_overwrites_source_even_with_force(tmp_path, force):
    source = tmp_path / "source.txt"
    original = "nu\u0308\u030c".encode()
    source.write_bytes(original)
    arguments = ["normalize", source, "--output", source]
    if force:
        arguments.append("--force")
    process = _run(*arguments)
    assert process.returncode == 2
    assert source.read_bytes() == original


@pytest.mark.parametrize("kind", ["hardlink", "symlink", "parent-symlink"])
def test_cli_refuses_alias_output_paths_even_with_force(tmp_path, kind):
    source = tmp_path / "source.txt"
    original = "nu\u0308\u030c".encode()
    source.write_bytes(original)
    output = tmp_path / "alias.txt"
    if kind == "hardlink":
        os.link(source, output)
    elif kind == "symlink":
        output.symlink_to(source)
    else:
        real_directory = tmp_path / "real"
        real_directory.mkdir()
        alias_directory = tmp_path / "alias"
        alias_directory.symlink_to(real_directory, target_is_directory=True)
        output = alias_directory / "out.txt"
    process = _run("normalize", source, "--output", output, "--force")
    assert process.returncode == 2 and source.read_bytes() == original
    if kind == "parent-symlink":
        assert not output.exists()


def test_cli_existing_output_requires_deliberate_force(tmp_path):
    source, destination = tmp_path / "source.txt", tmp_path / "out.txt"
    source.write_bytes("nu\u0308\u030c".encode())
    destination.write_bytes(b"previous output")
    first = _run("normalize", source, "--output", destination)
    assert first.returncode == 2 and destination.read_bytes() == b"previous output"
    second = _run("normalize", source, "--output", destination, "--force")
    assert second.returncode == 0 and destination.read_bytes() == "nǚ".encode()
    assert not list(tmp_path.glob(".language-guard-*"))


def test_cli_requires_existing_output_parent(tmp_path):
    source = tmp_path / "source.txt"
    source.write_bytes(b"text")
    destination = tmp_path / "absent" / "out.txt"
    process = _run("normalize", source, "--output", destination)
    assert process.returncode == 2 and not destination.exists()


def test_cli_audit_pass_writes_identical_json_report_outside_site(tmp_path):
    site = _site(tmp_path / "site")
    before = {p.name: p.read_bytes() for p in site.iterdir()}
    report = tmp_path / "audit.json"
    process = _run("audit", site, "--base-url", "https://study.invalid/", "--report", report)
    assert process.returncode == 0 and not process.stderr
    result = _status(process)
    assert result["ok"] is True and result["enabled"] is True
    assert result["pages"] == 2 and result["issues"] == []
    assert report.read_bytes() == process.stdout
    assert {p.name: p.read_bytes() for p in site.iterdir()} == before


def test_cli_audit_findings_return_one_and_structured_json(tmp_path):
    site = _site(tmp_path / "site")
    page = site / "index.html"
    page.write_bytes(page.read_bytes().replace(b'lang="en"', b'lang=""'))
    process = _run("audit", site, "--base-url", "https://study.invalid/")
    assert process.returncode == 1 and not process.stderr
    result = _status(process)
    assert result["ok"] is False
    assert any(issue["code"] == "lang.root" for issue in result["issues"])


def test_cli_audit_refuses_report_inside_site_even_with_force(tmp_path):
    site = _site(tmp_path / "site")
    target = site / "index.html"
    original = target.read_bytes()
    process = _run("audit", site, "--base-url", "https://study.invalid/", "--report", target, "--force")
    assert process.returncode == 2 and not process.stdout
    assert target.read_bytes() == original


def test_cli_disabled_audit_reports_skipped_without_reading_missing_root(tmp_path):
    process = _run("audit", tmp_path / "absent", "--base-url", "https://study.invalid/", env={_FLAG: "true"})
    assert process.returncode == 0
    result = _status(process)
    assert result["skipped"] is True and result["enabled"] is False and result["pages"] == 0


def test_cli_invalid_flag_returns_json_error_without_work(tmp_path):
    output = tmp_path / "out.txt"
    process = _run("normalize", tmp_path / "missing.txt", "--output", output, env={_FLAG: "maybe"})
    assert process.returncode == 2 and not process.stdout and not output.exists()
    assert _FLAG in _status(process)["error"]
