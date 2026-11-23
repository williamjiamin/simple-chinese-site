"""Build original fixtures and verify ON/OFF behavior without editing a site."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil

from .chinese_site_guard.display import load_payload, normalize_bytes, render_html
from .chinese_site_guard.flags import KILL_SWITCH, is_enabled
from .chinese_site_guard.html_audit import audit_site


@contextmanager
def _override(value):
    old = os.environ.get(KILL_SWITCH)
    try:
        if value is None:
            os.environ.pop(KILL_SWITCH, None)
        else:
            os.environ[KILL_SWITCH] = value
        yield
    finally:
        if old is None:
            os.environ.pop(KILL_SWITCH, None)
        else:
            os.environ[KILL_SWITCH] = old


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def main(argv=None) -> int:
    cli = argparse.ArgumentParser(description="Build and verify a new local example output directory")
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args(argv)
    source_root = Path(__file__).resolve().parents[1]
    templates = source_root / "examples" / "site"
    output = args.output.absolute()
    if output.exists() or any(p.is_symlink() for p in output.parents):
        cli.error("output must be a new path with no symbolic-link ancestors")
    before = snapshot(templates)
    original = (templates / "index.html").read_bytes()
    payload = load_payload((source_root / "examples" / "phrases.json").read_bytes())
    with _override(None):
        default_on = is_enabled()
        on = render_html(original, payload)
    with _override("1"):
        off = render_html(original, None, enabled=True)
        arbitrary = b"\xff\xfe\r\ninvalid UTF-8 is untouched when disabled\x00"
        raw_off = normalize_bytes(arbitrary, enabled=True)
    output.mkdir(parents=True)
    for mode, content in (("baseline", original), ("on", on), ("off", off)):
        shutil.copytree(templates, output / mode)
        (output / mode / "index.html").write_bytes(content)
    audits = {mode: audit_site(output / mode, base_url="https://study.invalid/")
              for mode in ("baseline", "on", "off")}
    checks = {
        "default_enabled": default_on,
        "enabled_display_changes_template": on != original,
        "kill_switch_overrides_explicit_on": off is original,
        "disabled_template_byte_equal": off == original,
        "disabled_invalid_utf8_byte_equal": raw_off is arbitrary,
        "source_files_unchanged": before == snapshot(templates),
        "fixture_audits_pass": all(result["ok"] for result in audits.values()),
        "fixture_audits_have_no_warnings": all(not result["issues"] for result in audits.values()),
    }
    report = {
        "order_id": "FW9-SIMPLECHINESE-SITE-01",
        "scope": "original local fixtures only; existing site source was not supplied",
        "checks": checks,
        "all": all(checks.values()),
        "baseline_sha256": hashlib.sha256(original).hexdigest(),
        "off_sha256": hashlib.sha256(off).hexdigest(),
        "on_sha256": hashlib.sha256(on).hexdigest(),
        "audits": audits,
    }
    (output / "fixture_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    print(f"ALL={'true' if report['all'] else 'false'}")
    return 0 if report["all"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
