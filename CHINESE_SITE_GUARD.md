# Offline language and page guard

Private implementation draft for `FW9-SIMPLECHINESE-SITE-01`.

The supplied input describes four HTML pages and a stylesheet but contains none
of their contents. This additive toolkit therefore leaves existing pages alone.
The examples are original implementation fixtures, not reconstructed site pages.
Run the audit on a local checkout to obtain findings for the real site.

## Run

Use Python 3.10 or newer. Runtime code uses only the standard library. The test
runner is `pytest`, installed separately; no dependency source is bundled.

From the directory containing `tools/`, `schemas/` and `examples/`:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
python3 -m tools.chinese_site_guard audit examples/site --base-url https://study.invalid/
python3 -m tools.verify_fixture --output /tmp/language-guard-preview
```

The example builder requires a new output directory. Open its `on/index.html`
for the compiled display or serve the directory with a local static server.
The `.invalid` canonical origin is deliberate and is never contacted.

Audit a real local site with the actual intended canonical HTTPS base:

```sh
python3 -m tools.chinese_site_guard audit /path/to/local/site \
  --base-url https://your-configured-origin.invalid/ \
  --report /path/outside/site/audit.json
```

The command prints structured JSON. Exit status `0` means the selected checks
passed; `1` means audit errors were found; `2` means command/data/I/O failure.
Warnings remain in the report and require review even when status is `0`.
The report output must be outside the audited tree. Existing outputs require
`--force`. Output aliases of an input file and symlink output paths are rejected.

## Enablement and exact rollback

`tools/chinese_site_guard/flags.py` is the single default: `DEFAULT_ENABLED=True`.
There is no opt-in step. The emergency environment override is:

```sh
CHINESE_SITE_GUARD_DISABLED=1 python3 -m tools.chinese_site_guard render \
  examples/site/index.html --phrases examples/phrases.json --output /tmp/off.html
```

The values `1`, `true`, `yes`, and `on` disable, case-insensitively. Empty,
`0`, `false`, `no`, and `off` leave the default in effect. Other values fail
configuration validation. An active kill switch wins over explicit API `True`.

Disabled rendering returns the original byte object before decoding, marker
inspection or payload validation. The CLI does not open the phrase file in
that mode. Disabled normalization preserves even malformed UTF-8 bytes. A
disabled audit reports `skipped: true` and clearly says no validation occurred.
Pure validation APIs (`audit_site`, `load_payload`, `mark_syllable`) always
validate; the switch controls adapters and phrase display.

The included baseline and OFF fixture have identical HTML and asset bytes.
The local browser launch was blocked by a required socket permission, so measured
pixel equality and keyboard checks were not executed. The actual site source
and rendering baseline were also absent; no actual-site pixel claim is made.

## Normalize UTF-8 and tone marks

```sh
python3 -m tools.chinese_site_guard normalize /path/input.txt --output /path/output.txt
```

This command strictly decodes UTF-8, normalizes to NFC, and writes a separate
file. It retains a leading byte-order mark and the existing newline sequences.
It never guesses an encoding or replaces bad bytes. NFC is canonical composition,
not compatibility folding; case and compatibility distinctions remain intact.
The standard definition is available at <https://www.unicode.org/reports/tr15/>.

The `mark_syllable` API separately converts one numbered pinyin syllable into
tone-mark form. It accepts `1`–`4`, neutral `0`/`5`, correctly placed existing
marks, decomposed marks, and `ü`/`v`/`u:` keyboard spellings. Examples:

| Input | Output |
| --- | --- |
| `ni3` | `nǐ` |
| `hao3` | `hǎo` |
| `liu2` | `liú` |
| `gui4` | `guì` |
| `lu:4` | `lǜ` |
| `ma5` | `ma` |

Multiple marks, mixed digits and marks, misplaced marks, unsupported combining
marks and malformed syllable syntax are rejected. Case is preserved. The parser
validates spelling structure, not membership in a pronunciation dictionary.
These tools can process author-supplied HSK learning content; no official
vocabulary corpus, level classification or examination alignment is included.

## Explicit tone-sandhi display

The data schema is `schemas/pinyin.phrases.schema.json`; runtime validation reads
that same schema rather than maintaining a second format declaration. Semantic
checks additionally reject duplicate phrase IDs, duplicate JSON keys, invalid
Unicode text and invalid pinyin. Every phrase supplies Hanzi, lexical pinyin,
an English translation and optional boolean context on each token.

- `boundary_after` prevents a rule from crossing the following boundary.
- `ordinal` preserves 一 in an explicitly annotated ordinal or counting context.
- `neutral` removes the spoken mark while preserving the known lexical tone.

Citation forms are retained separately. The spoken display supports adjacent
third-tone pairs, 一 before tones 1–4, and 不 before tone 4. 一/不 use the
following **lexical** tone, including a known lexical tone whose surface reading
is explicitly neutral. A neutral follower blocks third-tone pairing. If the
input supplies only neutral `5`, no underlying tone is inferred.

Third-tone runs use a deterministic adjacent-pair teaching policy within the
author-supplied group. Natural speech can require different grouping; this is
not a universal sentence pronunciation engine. It does not infer polyphones,
word boundaries, lexical neutral tones, half-third contours, regional readings
or discourse prosody. The examples are independently authored.

To compile a page, place exactly one `<!-- CHINESE_SITE_GUARD -->` marker where
the study section belongs, supply the annotated JSON and copy `display.js`
beside the output page:

```sh
python3 -m tools.chinese_site_guard render examples/site/index.html \
  --phrases examples/phrases.json --output /path/to/output/index.html
```

The compiler escapes all data values, writes only at the marker, and preserves
all surrounding source bytes. Keep its generated IDs (`tone-study`,
`tone-study-title`, `tone-mode`, `tone-examples`, and `phrase-<id>`) unused in
the template. Run the page audit after compiling to catch any ID collision.
The browser script switches precomputed citation/spoken text with `textContent`;
it contains no second tone algorithm, fetches nothing and stores no preferences.
The native button exposes pressed state and uses a polite live region inside a
labeled section. Keyboard behavior remains to be verified in a browser. Compiled
text is present in the HTML before any scripts run.

## Static audit policy

The audit strictly decodes local HTML and checks NFC, replacement characters,
early UTF-8 charset, a doctype, a document title and supported language tags.
Root pages must use `zh-Hans` or `en`; embedded pinyin may use
`zh-Latn-pinyin`. Visible Hanzi and selected text attributes require effective
`zh-Hans`, including supplementary ideographs. English and pinyin identification
remain human annotation tasks.

Accessibility checks cover one visible main region and h1, heading progression,
nonempty headings, alternative text, duplicate IDs, ID references, visible
control names, explicit/wrapped form labels and frame titles. Inactive template
content cannot satisfy these requirements. Referenced hidden labels can supply
accessible names; incidental hidden descendants cannot. Custom control roles
produce a review warning.

Each page needs one absolute canonical link in its head matching the configured
HTTPS origin and route. Root and nested `index.html` map to directory URLs;
other pages map to their relative filenames. Credentials, query strings,
fragments, unsafe schemes, traversal and ambiguous URL encoding are rejected.
Local links and fragments are resolved from the file tree. External navigation
is not fetched. The offline profile rejects recognized remote runtime assets
and form endpoints. Unsupported responsive-source lists, embedded HTML and CSS
URL constructs produce review warnings. External stylesheet content and script
behavior are not interpreted by this static checker.

The configured privacy page must be readable HTML with content beyond a heading,
and every other page must expose a visible link to it. This verifies presence
and reachability only. It does not certify policy truth, legal adequacy or all
possible data collection.

The parser is a bounded static approximation, not a browser DOM repair engine or
a complete accessible-name implementation. Computed CSS visibility, color
contrast, keyboard paths, runtime changes and assistive-technology behavior need
separate review. The package records the blocked browser attempt and verified
byte equality, not rendered-view or full accessibility certification.

Reference guidance for language, images and labels:
<https://www.w3.org/WAI/tips/developing/>. No reference source text or external
implementation code is bundled.
