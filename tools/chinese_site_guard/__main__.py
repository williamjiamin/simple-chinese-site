"""Offline command-line entry point. Exit codes: 0 pass, 1 findings, 2 error."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .display import load_payload, normalize_bytes, render_html
from .flags import KILL_SWITCH, is_enabled
from .html_audit import audit_site


def write_output(path: Path, data: bytes, *, source: Path | None = None, force: bool = False) -> None:
    path = path.absolute()
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("output paths may not contain symlinks")
    if source is not None and (path.resolve() == source.resolve() or (path.exists() and os.path.samefile(path, source))):
        raise ValueError("output must differ from input; original files are preserved")
    if path.exists() and not force:
        raise ValueError("output already exists; use --force for a deliberate replacement")
    if not path.parent.is_dir():
        raise ValueError("output parent directory must already exist")
    fd, staging = tempfile.mkstemp(prefix=".language-guard-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Offline tone, UTF-8 and static HTML verification")
    subs = cli.add_subparsers(dest="command", required=True)
    audit = subs.add_parser("audit", help="audit a local static site without fetching URLs")
    audit.add_argument("root", type=Path)
    audit.add_argument("--base-url", required=True)
    audit.add_argument("--privacy-path", default="privacy.html")
    audit.add_argument("--report", type=Path)
    audit.add_argument("--force", action="store_true")
    norm = subs.add_parser("normalize", help="normalize UTF-8 to NFC into a separate file")
    norm.add_argument("input", type=Path)
    norm.add_argument("--output", required=True, type=Path)
    norm.add_argument("--force", action="store_true")
    render = subs.add_parser("render", help="compile annotated phrases into a marked HTML template")
    render.add_argument("input", type=Path)
    render.add_argument("--phrases", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    render.add_argument("--force", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        active = is_enabled()
        if args.command == "audit":
            result = audit_site(args.root, base_url=args.base_url, privacy_path=args.privacy_path) if active else {
                "ok": True, "pages": 0, "issues": [], "scope": "static-offline", "skipped": True,
                "reason": f"{KILL_SWITCH} is active; no validation performed",
            }
            result["enabled"] = active
            encoded = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
            if args.report:
                # Reports must not be written into the tree being audited.
                try:
                    args.report.resolve().relative_to(args.root.resolve())
                except ValueError:
                    pass
                else:
                    raise ValueError("report path must be outside the audited site")
                write_output(args.report, encoded, force=args.force)
            sys.stdout.buffer.write(encoded)
            return 0 if result["ok"] else 1
        original = args.input.read_bytes()
        if args.command == "normalize":
            output = normalize_bytes(original, enabled=active)
        else:
            # A disabled adapter does not even open the optional phrase file.
            payload = load_payload(args.phrases.read_bytes()) if active else None
            output = render_html(original, payload, enabled=active)
        write_output(args.output, output, source=args.input, force=args.force)
        print(json.dumps({"ok": True, "enabled": active, "changed": output != original,
                          "bytes": len(output), "sha256": hashlib.sha256(output).hexdigest()}, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, UnicodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=True, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
