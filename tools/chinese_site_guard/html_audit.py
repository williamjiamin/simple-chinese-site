"""Read-only, network-free checks for a small bilingual static HTML site.

The checks are an explicit project policy, not a complete accessibility or
privacy certification. Text-language inference is deliberately limited to
visible Hanzi; English and transliteration language choices need human review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import os
import re
import stat
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit


_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_SKIP_TEXT = frozenset({"script", "style", "template"})
_LANGS = frozenset({"zh-Hans", "en", "zh-Latn-pinyin"})
_MAX_PAGE_BYTES = 4 * 1024 * 1024
_BAD_PERCENT = re.compile(r"%(?![0-9a-fA-F]{2})")
_HIDDEN_STYLE = re.compile(r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse))\b", re.I)


@dataclass(eq=False)
class _Node:
    tag: str
    attrs: dict[str, str]
    line: int
    parent: _Node | None = None
    children: list[_Node | str] = field(default_factory=list)


class _Document(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {}, 1)
        self.stack = [self.root]
        self.nodes: list[_Node] = []
        self.texts: list[tuple[_Node, str, int]] = []
        self.doctypes: list[str] = []
        self.duplicate_attrs: list[tuple[str, int]] = []
        self.charset_ends: dict[int, int] = {}
        self.source = text
        self.line_offsets = [0]
        for match in re.finditer("\n", text):
            self.line_offsets.append(match.end())
        self.feed(text)
        self.close()

    def handle_decl(self, decl: str) -> None:
        self.doctypes.append(decl)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.stack) > 128 or len(self.nodes) >= 32768:
            raise ValueError("HTML exceeds the audit limit of 128 nesting levels or 32768 elements")
        seen: set[str] = set()
        values: dict[str, str] = {}
        for key, value in attrs:
            if key in seen:
                self.duplicate_attrs.append((key, self.getpos()[0]))
            seen.add(key)
            values[key] = value if value is not None else ""
        node = _Node(tag, values, self.getpos()[0], self.stack[-1])
        if tag == "meta" and "charset" in values:
            line, column = self.getpos()
            end = self.line_offsets[line - 1] + column + len(self.get_starttag_text())
            self.charset_ends[id(node)] = len(self.source[:end].encode("utf-8"))
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)
        self.texts.append((self.stack[-1], data, self.getpos()[0]))


def _hidden(node: _Node) -> bool:
    current: _Node | None = node
    while current is not None:
        if ("hidden" in current.attrs or current.attrs.get("aria-hidden", "").lower() == "true"
                or _HIDDEN_STYLE.search(current.attrs.get("style", ""))):
            return True
        current = current.parent
    return False


def _inactive(node: _Node) -> bool:
    current: _Node | None = node
    while current is not None:
        if current.tag in _SKIP_TEXT:
            return True
        current = current.parent
    return False


def _text(node: _Node, *, include_hidden: bool = False) -> str:
    if node.tag in _SKIP_TEXT or (not include_hidden and _hidden(node)):
        return ""
    if node.tag == "img":
        return node.attrs.get("alt", "")
    parts: list[str] = []
    for child in node.children:
        parts.append(child if isinstance(child, str) else _text(child, include_hidden=include_hidden))
    return " ".join(" ".join(parts).split())


def _lang(node: _Node) -> str:
    current: _Node | None = node
    while current is not None:
        if "lang" in current.attrs:
            return current.attrs["lang"]
        current = current.parent
    return ""


def _has_hanzi(text: str) -> bool:
    return any(0x3400 <= ord(c) <= 0x4DBF or 0x4E00 <= ord(c) <= 0x9FFF
               or 0xF900 <= ord(c) <= 0xFAFF or 0x20000 <= ord(c) <= 0x2FA1F
               or 0x30000 <= ord(c) <= 0x3347F for c in text)


def _name(node: _Node, ids: dict[str, _Node]) -> str:
    labelledby = node.attrs.get("aria-labelledby", "").split()
    if labelledby:
        # Explicit references may name hidden content; normal descendant content may not.
        budget = [256]
        name = " ".join(_referenced_text(ids[key], ids, set(), budget) for key in labelledby if key in ids)
        if name.strip():
            return name.strip()
    if node.attrs.get("aria-label", "").strip():
        return node.attrs["aria-label"].strip()
    if node.tag == "input":
        kind = node.attrs.get("type", "text").lower()
        if kind in {"submit", "reset"}:
            return node.attrs.get("value", "Submit" if kind == "submit" else "Reset").strip()
        if kind == "image":
            return node.attrs.get("alt", "").strip()
        if kind == "button":
            return node.attrs.get("value", "").strip()
    return _text(node)


def _referenced_text(node: _Node, ids: dict[str, _Node], visited: set[int], budget: list[int]) -> str:
    if budget[0] <= 0:
        return ""
    budget[0] -= 1
    if id(node) in visited:
        return _text(node, include_hidden=True)
    visited = visited | {id(node)}
    references = node.attrs.get("aria-labelledby", "").split()
    if references:
        value = " ".join(_referenced_text(ids[key], ids, visited, budget) for key in references if key in ids)
        if value.strip():
            return value.strip()
    return node.attrs.get("aria-label", "").strip() or _text(node, include_hidden=True)


def _decoded_path(path: str) -> str:
    if _BAD_PERCENT.search(path):
        raise ValueError("Malformed percent escape in URL path")
    decoded = unquote(path, encoding="utf-8", errors="strict")
    # Nested escapes can be interpreted differently by downstream servers.
    if re.search(r"%[0-9a-fA-F]{2}", decoded):
        raise ValueError("Nested URL path encoding is not supported")
    if "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        raise ValueError("Backslashes and control characters are not allowed in paths")
    if any(segment == ".." for segment in decoded.split("/")):
        raise ValueError("Parent-directory traversal is not allowed in URLs")
    return decoded


def _origin(parts) -> tuple[str, str, int | None]:
    port = parts.port
    return parts.scheme.lower(), (parts.hostname or "").lower(), port if port is not None else (443 if parts.scheme.lower() == "https" else 80)


def _clean_url(value: str) -> bool:
    return isinstance(value, str) and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) and "\\" not in value


def _resolve(root: Path, source: str, href: str, base_url: str) -> tuple[Path | None, str]:
    """Resolve an internal route without opening or fetching it; external is None."""
    if not _clean_url(href):
        raise ValueError("Whitespace, control characters, and backslashes are not allowed in URLs")
    parts = urlsplit(href)
    base = urlsplit(base_url)
    if parts.scheme.lower() in {"javascript", "data", "vbscript", "file"}:
        raise ValueError("Unsafe or local-machine URL scheme")
    if parts.username is not None or parts.password is not None:
        raise ValueError("Credential-bearing URLs are not allowed")
    if parts.scheme and parts.scheme.lower() not in {"https", "http", "mailto", "tel"}:
        raise ValueError("Unsupported URL scheme")
    if parts.scheme.lower() in {"https", "http"} and not parts.netloc:
        raise ValueError("HTTP URLs must include an authority")
    if parts.scheme.lower() in {"mailto", "tel"}:
        if not parts.path:
            raise ValueError("Empty contact URL")
        return None, ""
    if parts.netloc:
        compare = parts if parts.scheme else urlsplit("https:" + href)
        if _origin(compare) != _origin(base):
            return None, ""
    decoded = _decoded_path(parts.path)
    base_path = _decoded_path(base.path).rstrip("/") + "/"
    if not decoded and parts.netloc:
        if base_path != "/":
            raise ValueError("URL path is outside the configured site base path")
        relative = PurePosixPath("index.html")
    elif not decoded:
        relative = PurePosixPath(source)
    elif decoded.startswith("/"):
        if not decoded.startswith(base_path):
            raise ValueError("URL path is outside the configured site base path")
        relative = PurePosixPath(decoded[len(base_path):])
    else:
        relative = PurePosixPath(source).parent / decoded
    candidate = root.joinpath(*relative.parts)
    if decoded.endswith("/") or candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("Resolved path escapes the site root")
    if _BAD_PERCENT.search(parts.fragment):
        raise ValueError("Malformed percent escape in URL fragment")
    fragment = unquote(parts.fragment, encoding="utf-8", errors="strict")
    return candidate, fragment


def audit_site(root: Path | str, *, base_url: str, privacy_path: str = "privacy.html") -> dict:
    """Audit local HTML files with deterministic issues and no network or writes.

    The origin is supplied by the owner; the tool does not guess public hosting.
    Canonical mapping: root/nested index.html -> its directory, other HTML ->
    its relative filename. Parent traversals, even contained ones, are rejected
    as a deliberate portable-static-site policy. Form posting endpoints and
    remote runtime assets are rejected because this profile is offline. The
    caller's root is canonicalized; symlinks within that resolved tree fail.
    """
    issues: list[dict] = []
    documents: dict[str, _Document] = {}

    def add(code: str, path: str, line: int, message: str, severity: str = "error") -> None:
        item = {"code": code, "severity": severity, "path": path, "line": line, "message": message}
        if item not in issues:
            issues.append(item)

    def finish() -> dict:
        issues.sort(key=lambda i: (i["path"], i["line"], i["code"], i["message"]))
        return {"ok": not any(i["severity"] == "error" for i in issues), "pages": len(documents),
                "issues": issues, "scope": "static-offline", "base_url": base_url,
                "limitations": ["No script execution or external requests; script behavior and CSS URL imports are not evaluated", "No computed style, contrast, focus, or assistive-technology certification; accessible names use a bounded static subset", "Privacy checks verify document presence and routes, not legal statements", "Language inference detects Hanzi only; review English and pinyin language spans manually"]}

    try:
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Site root is not a directory")
        if not _clean_url(base_url):
            raise ValueError("base_url must not contain whitespace, controls, or backslashes")
        base = urlsplit(base_url)
        if (base.scheme.lower() != "https" or not base.hostname or base.username is not None
                or base.password is not None or base.query or base.fragment):
            raise ValueError("base_url must be an absolute HTTPS site base without credentials, query, or fragment")
        _origin(base)
        _decoded_path(base.path)
        base_url = base_url.rstrip("/") + "/"
        if (not isinstance(privacy_path, str) or not privacy_path or PurePosixPath(privacy_path).is_absolute()
                or ".." in PurePosixPath(privacy_path).parts or "\\" in privacy_path
                or urlsplit(privacy_path).query or urlsplit(privacy_path).fragment
                or urlsplit(privacy_path).scheme):
            raise ValueError("privacy_path must be a relative HTML file within the site root")
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        add("config.invalid", "", 0, str(exc))
        return finish()

    html_paths: list[Path] = []
    def raise_walk_error(error: OSError) -> None:
        raise error
    try:
        for current, dirs, files in os.walk(root, followlinks=False, onerror=raise_walk_error):
            for name in list(dirs):
                item = Path(current) / name
                if item.is_symlink():
                    add("path.symlink", item.relative_to(root).as_posix(), 0, "Symbolic links are excluded from the offline site")
                    dirs.remove(name)
            for name in files:
                item = Path(current) / name
                if item.is_symlink():
                    add("path.symlink", item.relative_to(root).as_posix(), 0, "Symbolic links are excluded from the offline site")
                elif item.suffix.lower() in {".html", ".htm"}:
                    if stat.S_ISREG(item.stat().st_mode):
                        html_paths.append(item)
                    else:
                        add("path.type", item.relative_to(root).as_posix(), 0, "HTML inputs must be regular files")
    except OSError as exc:
        add("path.read", "", 0, str(exc))
        return finish()
    if not html_paths:
        add("site.empty", "", 0, "No HTML pages found")

    for page in sorted(html_paths):
        relative = page.relative_to(root).as_posix()
        try:
            if page.stat().st_size > _MAX_PAGE_BYTES:
                add("encoding.size", relative, 0, "HTML page exceeds the 4 MiB audit limit")
                continue
            data = page.read_bytes()
            text = data.decode("utf-8-sig", errors="strict")
            if unicodedata.normalize("NFC", text) != text:
                add("encoding.nfc", relative, 1, "Text must be NFC-normalized UTF-8")
            if "\ufffd" in text or "\x00" in text:
                add("encoding.replacement", relative, 1, "Text contains a replacement character or NUL")
            document = _Document(text)
            documents[relative] = document
        except UnicodeDecodeError:
            add("encoding.utf8", relative, 1, "HTML is not strict UTF-8")
            continue
        except (OSError, ValueError) as exc:
            add("html.read", relative, 0, str(exc))
            continue
        nodes = document.nodes
        roots = [n for n in nodes if n.tag == "html"]
        if len(roots) != 1 or roots[0].parent is not document.root:
            add("html.root", relative, 1, "Exactly one html root is required")
        elif roots[0].attrs.get("lang") not in {"zh-Hans", "en"}:
            add("lang.root", relative, roots[0].line, "The html root lang must be zh-Hans or en")
        for node in nodes:
            if "lang" in node.attrs and node.attrs["lang"] not in _LANGS:
                add("lang.unsupported", relative, node.line, "Supported language tags are zh-Hans, en, and zh-Latn-pinyin")
        if document.doctypes != ["DOCTYPE html"] and [d.lower() for d in document.doctypes] != ["doctype html"]:
            add("html.doctype", relative, 1, "Declare the HTML doctype exactly once")
        heads = [n for n in nodes if n.tag == "head" and n.parent in roots and not _inactive(n)]
        bodies = [n for n in nodes if n.tag == "body" and n.parent in roots and not _inactive(n)]
        if len(heads) != 1 or len(bodies) != 1:
            add("html.sections", relative, 1, "The html element needs one direct head and body")
        titles = [n for n in nodes if n.tag == "title" and _within(n, "head") and not _inactive(n)]
        if len(titles) != 1 or not _text(titles[0]).strip():
            add("html.title", relative, 1, "Exactly one nonempty title is required")
        if sum(n.tag == "main" and _within(n, "body") and not _hidden(n) and not _inactive(n) for n in nodes) != 1:
            add("a11y.main", relative, 1, "Exactly one visible main element is required")
        if sum(n.tag == "h1" and _within(n, "body") and not _hidden(n) and not _inactive(n) for n in nodes) != 1:
            add("a11y.h1", relative, 1, "Exactly one visible h1 is required by this site policy")
        charset_nodes = [n for n in nodes if n.tag == "meta" and "charset" in n.attrs and _within(n, "head") and not _inactive(n)]
        # Scan bytes so multibyte Hanzi cannot move an apparently early declaration past the byte limit.
        bom_bytes = 3 if data.startswith(b"\xef\xbb\xbf") else 0
        early_charset = bool(charset_nodes and document.charset_ends[id(charset_nodes[0])] + bom_bytes <= 1024)
        if len(charset_nodes) != 1 or charset_nodes[0].attrs.get("charset", "").lower() != "utf-8" or not early_charset:
            add("encoding.charset", relative, 1, "Declare one meta charset=utf-8 completely within the first 1024 bytes")
        if any(n.tag == "base" for n in nodes):
            add("link.base", relative, 1, "Base elements are unsupported by the explicit static route policy")
        for attr, line in document.duplicate_attrs:
            add("html.attribute_duplicate", relative, line, "Duplicate attribute: " + attr)
        ids: dict[str, _Node] = {}
        for node in nodes:
            if "id" in node.attrs:
                identifier = node.attrs["id"]
                if not identifier or any(c.isspace() for c in identifier):
                    add("a11y.id_invalid", relative, node.line, "IDs must be nonempty and contain no whitespace")
                elif identifier in ids:
                    add("a11y.id_duplicate", relative, node.line, "Duplicate ID: " + identifier)
                else:
                    ids[identifier] = node
        previous_heading = 0
        for node in nodes:
            if _inactive(node):
                continue
            for attr in ("aria-labelledby", "aria-describedby"):
                if attr in node.attrs:
                    references = node.attrs[attr].split()
                    if not references or any(key not in ids for key in references):
                        add("a11y.reference", relative, node.line, attr + " must reference existing IDs")
            if _hidden(node):
                continue
            if re.fullmatch(r"h[1-6]", node.tag):
                level = int(node.tag[1])
                if level > previous_heading + 1:
                    add("a11y.heading_order", relative, node.line, "Heading levels must not skip forward")
                if not _text(node):
                    add("a11y.heading_empty", relative, node.line, "Headings must have visible text")
                previous_heading = level
            if node.tag == "img" and "alt" not in node.attrs:
                add("a11y.image_alt", relative, node.line, "Images need alt text or an explicit empty decorative alt")
            if (node.tag == "a" and "href" in node.attrs) or node.tag == "button":
                if not _name(node, ids):
                    add("a11y.control_name", relative, node.line, "Links and buttons need a static accessible name")
            if node.tag in {"input", "select", "textarea"} and node.attrs.get("type", "text").lower() != "hidden":
                explicit = any(n.tag == "label" and n.attrs.get("for") == node.attrs.get("id") and "id" in node.attrs and _text(n, include_hidden=True) for n in nodes)
                parent = node.parent
                wrapped = False
                while parent is not None:
                    if parent.tag == "label" and _text(parent):
                        wrapped = True
                        break
                    parent = parent.parent
                own_name = bool(node.attrs.get("aria-label", "").strip() or (node.attrs.get("aria-labelledby") and _name(node, ids)))
                implicit = node.tag == "input" and node.attrs.get("type", "text").lower() in {"submit", "reset", "button", "image"} and bool(_name(node, ids))
                if not (explicit or wrapped or own_name or implicit):
                    add("a11y.form_label", relative, node.line, "Form controls need an associated label or accessible name")
            if node.tag == "iframe" and not node.attrs.get("title", "").strip():
                add("a11y.frame_title", relative, node.line, "Embedded frames need a descriptive title")
            if node.attrs.get("role") in {"button", "link", "checkbox", "radio", "combobox", "slider", "switch", "tab"} and node.tag not in {"a", "button", "input", "select"}:
                add("a11y.custom_control", relative, node.line, "Custom controls need manual keyboard, focus, state, and name review", "warning")
            for attr in ("alt", "title", "aria-label", "placeholder"):
                if _has_hanzi(node.attrs.get(attr, "")) and _lang(node) != "zh-Hans":
                    add("lang.hanzi", relative, node.line, "Hanzi text needs effective lang=zh-Hans")
        for owner, text_chunk, line in document.texts:
            if _inactive(owner) or _hidden(owner):
                continue
            if "\ufffd" in text_chunk:
                add("encoding.replacement", relative, line, "Decoded text contains a replacement character")
            if _has_hanzi(text_chunk) and _lang(owner) != "zh-Hans":
                add("lang.hanzi", relative, line, "Hanzi text needs effective lang=zh-Hans")
        canonicals = [n for n in nodes if n.tag == "link" and "canonical" in n.attrs.get("rel", "").lower().split() and _within(n, "head") and not _inactive(n)]
        expected_route = relative[:-10] if PurePosixPath(relative).name == "index.html" else relative
        expected = urljoin(base_url, expected_route)
        if len(canonicals) != 1:
            add("canonical.count", relative, 1, "Exactly one canonical link is required")
        else:
            canonical = canonicals[0]
            href = canonical.attrs.get("href", "")
            try:
                if not _clean_url(href):
                    raise ValueError("Canonical contains whitespace, controls, or backslashes")
                actual = urlsplit(href)
                want = urlsplit(expected)
                if (actual.scheme != "https" or not actual.netloc or actual.username is not None
                        or actual.password is not None or actual.query or actual.fragment
                        or _origin(actual) != _origin(want)
                        or _decoded_path(actual.path or "/") != _decoded_path(want.path or "/")):
                    raise ValueError("Canonical must equal the configured HTTPS page route")
            except (ValueError, UnicodeError):
                add("canonical.target", relative, canonical.line, "Canonical must equal the configured HTTPS page route without credentials, query, or fragment")

    privacy_target = root / privacy_path
    privacy_relative = PurePosixPath(privacy_path).as_posix()
    privacy_doc = documents.get(privacy_relative)
    if privacy_doc is None:
        add("privacy.missing", privacy_relative, 0, "The configured privacy page must exist as readable HTML")
    else:
        content = [n for n in privacy_doc.nodes if n.tag == "main" and not _inactive(n) and _text(n)]
        meaningful = any(text.strip() and not any(_within(owner, tag) for tag in {"h1", "h2", "h3", "h4", "h5", "h6", "nav", "footer", "a"})
                         and not _inactive(owner) and not _hidden(owner)
                         for owner, text, _ in privacy_doc.texts if _within(owner, "main"))
        if not content or not meaningful:
            add("privacy.empty", privacy_relative, 1, "Privacy page needs nonempty main content beyond a heading")

    for relative, document in sorted(documents.items()):
        privacy_linked = relative == privacy_relative
        for node in document.nodes:
            if _within(node, "template"):
                continue
            destinations: list[tuple[str, str]] = []
            if node.tag == "a" and "href" in node.attrs:
                destinations.append(("link", node.attrs["href"]))
            if node.tag in {"script", "img", "iframe", "audio", "video", "source", "track", "embed"} and "src" in node.attrs:
                destinations.append(("asset", node.attrs["src"]))
            if node.tag == "link" and {"stylesheet", "preload", "prefetch", "modulepreload", "icon"}.intersection(node.attrs.get("rel", "").lower().split()):
                destinations.append(("asset", node.attrs.get("href", "")))
            if node.tag == "object" and "data" in node.attrs:
                destinations.append(("asset", node.attrs["data"]))
            if node.tag in {"image", "use"}:
                for attr in ("href", "xlink:href"):
                    if attr in node.attrs:
                        destinations.append(("asset", node.attrs[attr]))
            if node.tag == "video" and "poster" in node.attrs:
                destinations.append(("asset", node.attrs["poster"]))
            if "srcset" in node.attrs or "imagesrcset" in node.attrs or "srcdoc" in node.attrs:
                add("offline.unchecked", relative, node.line, "Responsive-source lists and embedded HTML need manual offline review", "warning")
            css_text = node.attrs.get("style", "") + (" ".join(c for c in node.children if isinstance(c, str)) if node.tag == "style" else "")
            if re.search(r"url\s*\(|@import", css_text, re.I):
                add("offline.css_unchecked", relative, node.line, "CSS URL and import destinations need manual offline review", "warning")
            if node.tag == "form" and "action" in node.attrs:
                destinations.append(("form", node.attrs["action"]))
            for kind, href in destinations:
                if not href.strip():
                    add("link.empty", relative, node.line, "Empty URL")
                    continue
                try:
                    target, fragment = _resolve(root, relative, href, base_url)
                    if target is None:
                        if kind in {"asset", "form"}:
                            add("offline.remote", relative, node.line, "Remote runtime assets and form endpoints are not allowed in the offline profile")
                        elif urlsplit(href).scheme.lower() == "http":
                            add("link.insecure", relative, node.line, "External navigation should use HTTPS", "warning")
                        continue
                    if not target.is_file():
                        add("link.missing", relative, node.line, "Local target does not exist: " + target.relative_to(root).as_posix())
                        continue
                    target_relative = target.relative_to(root).as_posix()
                    if fragment:
                        target_doc = documents.get(target_relative)
                        if target_doc is None:
                            add("link.fragment_unchecked", relative, node.line, "Fragment target is not parsed HTML", "warning")
                        elif not any(n.attrs.get("id") == fragment or (n.tag == "a" and n.attrs.get("name") == fragment) for n in target_doc.nodes):
                            add("link.fragment", relative, node.line, "Fragment ID does not exist: " + fragment)
                    if kind == "link" and target.resolve() == privacy_target.resolve() and not _hidden(node):
                        privacy_linked = True
                except (ValueError, UnicodeError, OSError) as exc:
                    add("link.invalid", relative, node.line, str(exc))
        if not privacy_linked:
            add("privacy.link", relative, 1, "Each page must expose a local link to the configured privacy page")
    return finish()


def _within(node: _Node, tag: str) -> bool:
    current: _Node | None = node
    while current is not None:
        if current.tag == tag:
            return True
        current = current.parent
    return False
