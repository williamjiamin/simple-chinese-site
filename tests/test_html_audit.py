"""Original fixtures exercise independent static-site policy failure modes."""

from pathlib import Path
import os
import unicodedata

import pytest

from tools.chinese_site_guard.html_audit import audit_site


BASE = "https://study.invalid/"


def page(route="", body="<p>Learning can be done offline.</p>", lang="en", extra_head=""):
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            f'<title>Language study</title><link rel="canonical" href="{BASE}{route}">{extra_head}'
            f'</head><body><main><h1>Language study</h1>{body}</main>'
            '<footer><a href="/privacy.html">Privacy</a></footer></body></html>')


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(page(), encoding="utf-8")
    (tmp_path / "privacy.html").write_text(page("privacy.html", "<p>This static sample sends no form data.</p>"), encoding="utf-8")
    return tmp_path


def result(site, **kwargs):
    return audit_site(site, base_url=BASE, **kwargs)


def codes(site, **kwargs):
    return {i["code"] for i in result(site, **kwargs)["issues"]}


def change(site, old, new, filename="index.html"):
    target = site / filename
    text = target.read_text(encoding="utf-8")
    assert old in text
    target.write_text(text.replace(old, new), encoding="utf-8")


def test_valid_bilingual_site_and_read_only(site):
    change(site, "Learning can be done offline.", '<span lang="zh-Hans">你好，𠀀。</span><span lang="zh-Latn-pinyin">nǐ hǎo</span>')
    before = {p.name: p.read_bytes() for p in site.iterdir()}
    report = result(site)
    assert report["ok"], report
    assert report["pages"] == 2
    assert before == {p.name: p.read_bytes() for p in site.iterdir()}
    assert result(site) == report


@pytest.mark.parametrize("replacement,code", [
    ('<html>', "lang.root"),
    ('<html lang="zh">', "lang.root"),
    ('<html lang="en-US">', "lang.root"),
    ('<html lang="en" lang="zh-Hans">', "html.attribute_duplicate"),
])
def test_root_language_policy(site, replacement, code):
    change(site, '<html lang="en">', replacement)
    assert code in codes(site)


@pytest.mark.parametrize("text", ["你", "𠀀", "𰀀", "㐀", "豈"])
def test_hanzi_needs_effective_language(site, text):
    change(site, "Learning can be done offline.", text)
    assert "lang.hanzi" in codes(site)


def test_hanzi_attributes_and_hidden_text(site):
    change(site, "Learning can be done offline.", '<button aria-label="读">Read</button><span hidden>你</span>')
    report = result(site)
    assert len([i for i in report["issues"] if i["code"] == "lang.hanzi"]) == 1


@pytest.mark.parametrize("raw,code", [(b"\xff", "encoding.utf8"), ("\ufffd".encode(), "encoding.replacement"), (b"\x00", "encoding.replacement")])
def test_invalid_encoding(site, raw, code):
    target = site / "index.html"
    target.write_bytes(target.read_bytes().replace(b"Learning", raw))
    assert code in codes(site)


def test_nfc_and_entity_replacement(site):
    change(site, "Learning", unicodedata.normalize("NFD", "nǐ"))
    assert "encoding.nfc" in codes(site)
    (site / "index.html").write_text(page(body="<p>&#xfffd;</p>"), encoding="utf-8")
    assert "encoding.replacement" in codes(site)


def test_utf8_bom_is_accepted(site):
    target = site / "index.html"
    target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())
    assert result(site)["ok"]


@pytest.mark.parametrize("meta", ['<meta charset="latin-1">', '', '<meta charset="utf-8"><meta charset="utf-8">'])
def test_charset_required_once(site, meta):
    change(site, '<meta charset="utf-8">', meta)
    assert "encoding.charset" in codes(site)


def test_charset_byte_limit_and_comment_cannot_spoof(site):
    change(site, '<meta charset="utf-8">', '<!-- <meta charset="utf-8">' + "你" * 400 + '--><meta charset="utf-8">')
    assert "encoding.charset" in codes(site)


@pytest.mark.parametrize("needle,replacement,code", [
    ("<!doctype html>", "", "html.doctype"),
    ("<title>Language study</title>", "<title> </title>", "html.title"),
    ("<main>", "<div>", "a11y.main"),
    ("<h1>Language study</h1>", "<h2>Language study</h2>", "a11y.h1"),
    ("<p>Learning can be done offline.</p>", "<h3>Section</h3>", "a11y.heading_order"),
    ("<p>Learning can be done offline.</p>", '<h2> </h2>', "a11y.heading_empty"),
])
def test_structure_checks(site, needle, replacement, code):
    change(site, needle, replacement)
    assert code in codes(site)


@pytest.mark.parametrize("body,code", [
    ('<img src="tile.svg">', "a11y.image_alt"),
    ('<button><span hidden>Go</span></button>', "a11y.control_name"),
    ('<button><span aria-hidden="true">Go</span></button>', "a11y.control_name"),
    ('<button><span style="display: none">Go</span></button>', "a11y.control_name"),
    ('<a href="privacy.html"><img src="tile.svg" alt=""></a>', "a11y.control_name"),
    ('<span id="same">a</span><span id="same">b</span>', "a11y.id_duplicate"),
    ('<button aria-labelledby="missing">Go</button>', "a11y.reference"),
    ('<input placeholder="Enter a term">', "a11y.form_label"),
    ('<iframe src="privacy.html"></iframe>', "a11y.frame_title"),
])
def test_accessibility_failures(site, body, code):
    (site / "tile.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    change(site, "<p>Learning can be done offline.</p>", body)
    assert code in codes(site)


@pytest.mark.parametrize("body", [
    '<button aria-labelledby="a b"></button><span id="a" hidden>Read</span><span id="b" aria-hidden="true">aloud</span>',
    '<button aria-labelledby="a"></button><span id="a" aria-label="Read"></span>',
    '<button aria-labelledby="a"></button><span id="a" aria-labelledby="b">Read</span><span id="b" aria-labelledby="a"></span>',
    '<label for="term">Term</label><input id="term">',
    '<label for="term" hidden>Term</label><input id="term">',
    '<label>Term <input></label>',
    '<input aria-label="Term"><select aria-label="Mode"><option>One</option></select>',
    '<a href="privacy.html"><img src="tile.svg" alt="Privacy"></a>',
])
def test_accessible_names_and_labels(site, body):
    (site / "tile.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    change(site, "<p>Learning can be done offline.</p>", body)
    assert result(site)["ok"], result(site)


@pytest.mark.parametrize("href", [
    "https://elsewhere.invalid/", "http://study.invalid/", "//study.invalid/", "/", "/index.html",
    "https://study.invalid/?x=1", "https://study.invalid/#part", "https://person@study.invalid/",
    "https://study.invalid/%2e%2e/", "https://study.invalid/%252e%252e/", "https://study.invalid/%zz/",
])
def test_canonical_rejects_ambiguous_or_wrong_target(site, href):
    change(site, f'rel="canonical" href="{BASE}"', f'rel="canonical" href="{href}"')
    assert "canonical.target" in codes(site)


def test_canonical_count_and_rel_token(site):
    change(site, '<link rel="canonical"', '<link rel="alternate canonical"')
    assert result(site)["ok"]
    change(site, "</head>", f'<link rel="canonical" href="{BASE}"></head>')
    assert "canonical.count" in codes(site)


def test_non_index_filename_keeps_full_route(site):
    (site / "myindex.html").write_text(page("myindex.html"), encoding="utf-8")
    assert result(site)["ok"], result(site)


def test_nested_index_and_base_subdirectory(site):
    (site / "unit").mkdir()
    (site / "unit/index.html").write_text(page("unit/", '<a href="/privacy.html">Policy</a>'), encoding="utf-8")
    assert result(site)["ok"], result(site)
    for path in site.rglob("*.html"):
        path.write_text(path.read_text().replace(BASE, BASE + "study/").replace('href="/privacy.html"', 'href="/study/privacy.html"'), encoding="utf-8")
    assert audit_site(site, base_url=BASE + "study/")["ok"]


@pytest.mark.parametrize("href,code", [
    ("missing.html", "link.missing"), ("#absent", "link.fragment"), ("privacy.html#absent", "link.fragment"),
    ("%2e%2e/private.html", "link.invalid"), ("%252e%252e/private.html", "link.invalid"),
    ("folder%5cprivate.html", "link.invalid"), ("%ff.html", "link.invalid"),
    ("%zz.html", "link.invalid"), ("../privacy.html", "link.invalid"),
    ("javascript:alert(1)", "link.invalid"), ("data:text/html,hello", "link.invalid"),
    ("file:///tmp/private", "link.invalid"), ("https:privacy.html", "link.invalid"),
    ("https://person@study.invalid/privacy.html", "link.invalid"), ("", "link.empty"),
])
def test_local_links_and_unsafe_urls(site, href, code):
    change(site, "Learning can be done offline.", f'<a href="{href}">Read</a>')
    assert code in codes(site)


def test_fragment_and_root_routes(site):
    change(site, "Learning can be done offline.", '<p id="part">Part</p><a href="#part">Section</a><a href="/?view=plain">Home</a><a href="https://study.invalid">Root</a>')
    assert result(site)["ok"], result(site)


@pytest.mark.parametrize("body", [
    '<script src="https://elsewhere.invalid/runtime.js"></script>',
    '<script src="//elsewhere.invalid/runtime.js"></script>',
    '<iframe title="Exercise" src="https://elsewhere.invalid/exercise"></iframe>',
    '<form action="https://elsewhere.invalid/submit"><button>Send</button></form>',
    '<img alt="Study image" src="https://elsewhere.invalid/image.png">',
    '<link rel="stylesheet" href="https://elsewhere.invalid/style.css">',
])
def test_remote_runtime_dependencies_are_rejected(site, body):
    change(site, "Learning can be done offline.", body)
    assert "offline.remote" in codes(site)


def test_local_script_is_checked_and_external_navigation_allowed(site):
    change(site, "Learning can be done offline.", '<script src="display.js"></script><a href="https://reading.invalid/">Read more</a>')
    assert "link.missing" in codes(site)
    (site / "display.js").write_text("'use strict';", encoding="utf-8")
    assert result(site)["ok"], result(site)


def test_privacy_required_and_link_must_be_visible(site):
    change(site, '<a href="/privacy.html">Privacy</a>', '<a hidden href="/privacy.html">Privacy</a>')
    assert "privacy.link" in codes(site)
    (site / "privacy.html").unlink()
    assert "privacy.missing" in codes(site)


def test_privacy_content_beyond_heading(site):
    (site / "privacy.html").write_text(page("privacy.html", ""), encoding="utf-8")
    assert "privacy.empty" in codes(site)


def test_symlink_escape_rejected(site, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "private.html").write_text(page("private.html"), encoding="utf-8")
    (site / "escape.html").symlink_to(outside / "private.html")
    (site / "escape").symlink_to(outside, target_is_directory=True)
    change(site, "Learning can be done offline.", '<a href="escape.html">Escape</a>')
    assert {"path.symlink", "link.invalid"} <= codes(site)


@pytest.mark.parametrize("base", ["http://study.invalid/", "https://study.invalid/?x=1", "https://person@study.invalid/", "", "https://study.invalid/%2e%2e/"])
def test_config_errors_reported(site, base):
    report = audit_site(site, base_url=base)
    assert not report["ok"]
    assert report["issues"][0]["code"] == "config.invalid"


@pytest.mark.parametrize("privacy", ["../privacy.html", "/privacy.html", "", "https://study.invalid/privacy.html", "privacy.html#x"])
def test_privacy_path_is_contained(site, privacy):
    assert "config.invalid" in codes(site, privacy_path=privacy)


def test_empty_and_nonexistent_root_fail(tmp_path):
    assert "site.empty" in {i["code"] for i in audit_site(tmp_path, base_url=BASE)["issues"]}
    assert "config.invalid" in {i["code"] for i in audit_site(tmp_path / "absent", base_url=BASE)["issues"]}


def test_report_does_not_claim_legal_or_full_a11y_certification(site):
    limitations = " ".join(result(site)["limitations"])
    assert "not legal" in limitations
    assert "No computed style" in limitations


def test_inactive_landmarks_cannot_satisfy_page_policy(site):
    change(site, "<main>", "<template><main>")
    change(site, "</main>", "</main></template>")
    assert {"a11y.main", "a11y.h1"} <= codes(site)


@pytest.mark.parametrize("markup,code", [
    ('<title>Language study</title>', "html.title"),
    ('<meta charset="utf-8">', "encoding.charset"),
    (f'<link rel="canonical" href="{BASE}">', "canonical.count"),
])
def test_inactive_metadata_does_not_satisfy_document_policy(site, markup, code):
    change(site, markup, "<template>" + markup + "</template>")
    assert code in codes(site)


def test_nested_heading_text_is_not_privacy_body(site):
    (site / "privacy.html").write_text(page("privacy.html", "").replace("<h1>Language study</h1>", "<h1><span>Privacy</span></h1>"), encoding="utf-8")
    assert "privacy.empty" in codes(site)


@pytest.mark.parametrize("body", [
    '<object data="https://elsewhere.invalid/frame.html"></object>',
    '<svg><image href="https://elsewhere.invalid/image.svg"></image></svg>',
    '<svg><use xlink:href="https://elsewhere.invalid/image.svg#shape"></use></svg>',
    '<video poster="https://elsewhere.invalid/image.png"></video>',
    '<link rel="preload" href="https://elsewhere.invalid/asset.bin">',
])
def test_additional_remote_asset_attributes(site, body):
    change(site, "Learning can be done offline.", body)
    assert "offline.remote" in codes(site)


@pytest.mark.parametrize("body,code", [
    ('<img alt="Image" srcset="small.png 1x, big.png 2x">', "offline.unchecked"),
    ('<iframe title="Frame" srcdoc="&lt;p&gt;Content&lt;/p&gt;"></iframe>', "offline.unchecked"),
    ('<p style="background: url(image.png)">Card</p>', "offline.css_unchecked"),
    ('<style>@import "site.css";</style>', "offline.css_unchecked"),
    ('<span role="button">Read</span>', "a11y.custom_control"),
])
def test_unsupported_runtime_constructs_report_review_warning(site, body, code):
    change(site, "Learning can be done offline.", body)
    report = result(site)
    assert any(i["code"] == code and i["severity"] == "warning" for i in report["issues"])


def test_absolute_origin_outside_base_prefix_is_rejected(site):
    for path in site.rglob("*.html"):
        path.write_text(path.read_text().replace(BASE, BASE + "study/").replace('href="/privacy.html"', 'href="/study/privacy.html"'), encoding="utf-8")
    change(site, "Learning can be done offline.", '<a href="https://study.invalid">Outside base</a>')
    report = audit_site(site, base_url=BASE + "study/")
    assert any(i["code"] == "link.invalid" for i in report["issues"])


@pytest.mark.parametrize("href", ["https://study.invalid:0/", "https://study invalid/", "https://study.invalid/\t", "https://study.invalid/\\", " https://study.invalid/"])
def test_canonical_port_and_raw_url_validation(site, href):
    change(site, f'rel="canonical" href="{BASE}"', f'rel="canonical" href="{href}"')
    assert "canonical.target" in codes(site)


@pytest.mark.parametrize("base", ["https://study invalid/", "https://study.invalid/\t", "https://study.invalid/\\", " https://study.invalid/"])
def test_base_url_raw_whitespace_and_controls_rejected(site, base):
    assert audit_site(site, base_url=base)["issues"][0]["code"] == "config.invalid"


import pytest

@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="os.mkfifo requires POSIX")
def test_fifo_html_is_not_opened(site):
    os.mkfifo(site / "stream.html")
    assert "path.type" in codes(site)


def test_deep_nesting_reports_error_without_recursion_crash(site):
    change(site, "Learning can be done offline.", "<div>" * 200 + "Text" + "</div>" * 200)
    assert "html.read" in codes(site)


def test_link_only_privacy_page_is_empty(site):
    (site / "privacy.html").write_text(page("privacy.html", '<nav><a href="/">Home</a></nav>'), encoding="utf-8")
    assert "privacy.empty" in codes(site)


def test_long_label_reference_chain_is_bounded(site):
    labels = ''.join(f'<span id="label{i}" aria-labelledby="label{i + 1}"></span>' for i in range(300))
    labels += '<span id="label300">Read</span>'
    change(site, "Learning can be done offline.", '<button aria-labelledby="label0"></button>' + labels)
    assert "a11y.control_name" in codes(site)
