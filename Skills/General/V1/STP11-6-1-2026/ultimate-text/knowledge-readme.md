# Ultimate Text - Skill Tool

One runnable, dependency-free Python Skill Tool that implements 240 text capabilities across 20 categories: 211 deterministic local capabilities and 29 agent-assisted capabilities that emit a validated task contract for the calling assistant to fill in.

It is built to be dropped into a larger assistant skill as one folder. There is no network access, no model call, no package install and no hidden state: every result is produced by ordinary Python from the text you give it, and every failure is a structured error with a stable code.

**Primary entry point:** `tools-ultimate-text.py` - one command line, twelve subcommands, 240 capabilities.

---

## 1. Quick start

Run every command from inside this folder (the folder that contains `tools-ultimate-text.py`).

```bash
# 1. help and the capability index
python3 tools-ultimate-text.py help
python3 tools-ultimate-text.py list --categories
python3 tools-ultimate-text.py list --category typography --format table
python3 tools-ultimate-text.py search caption

# 2. inspect one capability: parameters, defaults, limits, acceptance checks
python3 tools-ultimate-text.py info typography.typography-cleaner

# 3. run a capability
python3 tools-ultimate-text.py run typography.typography-cleaner --text 'He said "hi"...  done.'
python3 tools-ultimate-text.py run creation.plain-language-converter --text-file draft.txt \
    --set strength=aggressive --json --out cleaned.txt
python3 tools-ultimate-text.py run analysis.sentiment-and-tone-analyzer --stdin < article.txt

# 4. verify the installation
python3 tools-ultimate-text.py selftest
python3 tools-ultimate-text.py config
python3 tools-ultimate-text.py assets
```

Every command accepts `--json` for the full envelope, `--out FILE` to write the primary output, `--approve` for gated actions and `--timeout SECONDS` to bound a run.

---

## 2. Requirements, setup and dependencies

| Requirement | Detail |
| --- | --- |
| Python | 3.9 or newer, standard library only. Developed and tested on CPython 3.13 (Linux). |
| Third-party packages | none. No `pip install` step is required or supported for core use. |
| Network | never used. No capability calls an API, model or web service. |
| Disk | reads only the files you name plus the `asset-*.json` files in this folder. |
| Platform | any OS with Python 3.9+. Paths inside the tool are handled with `os.path`, and text files are read in UTF-8 unless you pass `--encoding`. |

Setup is: copy the folder, then run `python3 tools-ultimate-text.py selftest`. There is nothing to build and nothing to configure before first use. If you want persistent defaults, copy `asset-config-defaults.json` outside the folder, edit it and pass `--config <file>`.

Windows note: use `python` instead of `python3` if that is how Python is installed. Long `--text` arguments with double quotes need the shell's own quoting rules; `--text-file` avoids the problem and keeps the input byte-exact.

---

## 3. What is in the folder (26 files)

Every file is flat inside this one folder and carries a `tools-`, `knowledge-` or `asset-` prefix. There are no subfolders, no `SKILL.md`, no manifest and no nested archive.

| File | Type | Role |
| --- | --- | --- |
| `asset-art-tables.json` | asset | Text-art data: 3x5 banner glyphs, boxes, mosaics, ornaments, kaomoji parts, emoji index, mood and flag tables. |
| `asset-capability-catalog.json` | asset | The 240-capability registry: ids, names, descriptions, parameters, engine, approval flags, timeouts, acceptance checks. |
| `asset-config-defaults.json` | asset | Runtime defaults and the documented config keys; copy, edit and load with `--config`. |
| `asset-lexicons-language.json` | asset | Lexicons: stopwords, hedges, fluff, jargon, inclusive terms, sensitive topics, sentiment, abbreviations, TTS units, typo/OCR maps. |
| `asset-patterns-security.json` | asset | Detection patterns for PII, secrets, prompt injection, covert channels, plus accessibility and recovery policies. |
| `asset-prompt-templates.json` | asset | Prompt templates and per-category directives used to build task contracts for agent-assisted capabilities. |
| `asset-unicode-tables.json` | asset | Unicode data: invisible characters, confusables, transliteration and romanisation tables, symbol maps, keyboard shortcuts, standards index. |
| `knowledge-readme.md` | knowledge | This document - the only knowledge file. |
| `tools-analysis-ops.py` | tools | Analysis handlers (extraction, topics, sentiment, arguments, concordance, timelines) and the local retrieval handlers. |
| `tools-art-ops.py` | tools | Text-art and emoji handlers (banners, boxes, mosaics, kaomoji, emoji indexing). |
| `tools-automation-ops.py` | tools | Automation handlers (file-wide replace plans, rename plans, pipelines, linters, QA, determinism, readiness gate). |
| `tools-contract-ops.py` | tools | Task contracts for the 29 agent-assisted capabilities plus output validation against acceptance checks. |
| `tools-core.py` | tools | Shared runtime: error classes, hard limits, config loading, path resolution, atomic writes, timeouts/retries, approval gate, text helpers. |
| `tools-doc-ops.py` | tools | Professional document and creative writing handlers (meetings, templates, learning material, poetry, scripts, naming, lore). |
| `tools-lang-ops.py` | tools | Language and typography handlers (script detection, transliteration, locale casing, plurals, quotes, numerals, dates, citations). |
| `tools-loader.py` | tools | Loads the flat hyphenated module files by path, injects shared namespaces, collects handlers. |
| `tools-media-ops.py` | tools | Media handlers (transcripts, captions/SRT/VTT, OCR cleanup, teleprompter, TTS prep, chapters, on-screen text). |
| `tools-meta-ops.py` | tools | Meta handlers (routing, requirements, contracts, verification, repair, composition, knowledge index, catalog maintenance). |
| `tools-notation-ops.py` | tools | Notation handlers (maths, logic, IPA, music, chess, braille, box drawing, currency, editorial marks, arrows). |
| `tools-registry.py` | tools | Dependency-free catalog access: parameter coercion/validation, capability lookup, search. |
| `tools-safety-ops.py` | tools | Safety, accessibility, integrity and provenance handlers (PII, secrets, injection, homoglyphs, checksums, redaction checks). |
| `tools-selftest.py` | tools | Bundled fixtures and structural checks behind `selftest`. |
| `tools-structure-ops.py` | tools | Structured-format and code-text handlers, including a self-contained YAML subset parser and CSV toolkit. |
| `tools-text-ops.py` | tools | Creation and editing handlers plus the reusable text helpers (syllables, readability, summarisation, case-preserving replacement). |
| `tools-ultimate-text.py` | tools | PRIMARY entry point: CLI, argument parsing, JSON envelope, error mapping, all 12 subcommands. |
| `tools-unicode-ops.py` | tools | Unicode and legacy-charset handlers. |

Counts: 18 `tools-` files, 7 `asset-` files, 1 `knowledge-` file. Total package size is small enough to copy by hand; the selftest asserts the prefix, subfolder and single-knowledge-file rules.

---

## 4. Commands, inputs, outputs and contracts

### 4.1 Subcommands

| Command | Purpose | Example |
| --- | --- | --- |
| `help [topic]` | General help, or the parameters of one capability / command. | `help run` |
| `list` | Capability index. Filters: `--category`, `--engine`, `--search`, `--limit`, `--categories`. Formats: `table`, `json`, `markdown`, `ids`. | `list --engine local --format ids` |
| `search <term>` | Keyword search over ids, names and descriptions. | `search emoji` |
| `info <cap>` | Full capability detail: description, parameters, defaults, approval flag, timeout, acceptance checks, storage. | `info safety.privacy-and-pii-redactor` |
| `run <cap>` | Execute a local capability, or emit the contract for an agent-assisted one. | `run typography.typography-cleaner --text '...'` |
| `contract <cap>` | Emit the task contract (prompt, inputs, output schema, checks) as JSON, markdown or text. | `contract creative.story-and-fiction-composer --format json` |
| `validate <cap>` | Check produced text against that capability's acceptance checks. | `validate creative.story-and-fiction-composer --text-file draft.md` |
| `selftest` | Run the bundled fixtures and structural checks. | `selftest --filter typography` |
| `config` | Print the effective merged configuration as JSON. | `config` |
| `assets` | List the shipped asset files, one per line. | `assets` |
| `capabilities` | Alias for `list`. | `capabilities --format markdown` |

### 4.2 Input flags

| Flag | Meaning |
| --- | --- |
| `--text TEXT` | Primary input inline. |
| `--text-file PATH` | Primary input from a file (alias `--input`). Relative paths resolve against `--cwd`, then `workspace_root`. |
| `--stdin` | Read the primary input from standard input. |
| `--text2 TEXT` / `--text2-file PATH` | Second text, used by diff, merge, compare and conflict capabilities. |
| `--text3 TEXT` / `--text3-file PATH` | Base text for three-way merges. |
| `--set name=value` | Capability parameter, repeatable. List values accept `a,b` or JSON. |
| `--params JSON` | All parameters at once as a JSON object. |
| `--cwd DIR` | Base directory for relative paths in this invocation. |
| `--encoding NAME` | Input/output encoding, for example `cp1252` when reading legacy files. |

### 4.3 Output flags

| Flag | Meaning |
| --- | --- |
| *(default)* | Primary output to stdout, warnings and diagnostics to stderr. |
| `--json` | Full JSON envelope to stdout, including `data`, `warnings` and `elapsed_ms`. |
| `--out FILE` | Write the primary output atomically to a file. Refuses to overwrite unless `--overwrite` is given or `allow_overwrite` is configured. |
| `--quiet` | Suppress the human summary. |
| `--verbose` | Print diagnostics to stderr. |

### 4.4 The JSON envelope

Every `--json` response has the same shape, success or failure:

```json
{
  "tool": "ultimate-text",
  "version": "1.0.0",
  "operation": "run",
  "status": "ok",
  "capability": {"id": "typography.typography-cleaner", "name": "...", "category": "...",
                 "engine": "local", "requires_approval": false},
  "params": {"style": "auto"},
  "inputs": [{"role": "primary", "chars": 42, "sha256": "..."}],
  "output": "primary text result",
  "data": {"...capability specific structured fields..."},
  "warnings": ["human-readable caution"],
  "diagnostics": ["why the result looks like this"],
  "elapsed_ms": 12.4
}
```

On failure `output`/`data` are absent and `error` is present with `code`, `message`, `hint` and `details`. The capability that failed is still named, so a batch caller can attribute errors.

### 4.5 Error codes and exit codes

| Code | Exit | Meaning | Recovery |
| --- | --- | --- | --- |
| `INVALID_INPUT` | 2 | Missing, too short or unparseable input. | Read the `hint`; supply more text or the right shape. |
| `INVALID_PARAM` | 2 | Unknown parameter, bad enum value, out-of-range number. | `info <cap>` lists every accepted parameter and default. |
| `UNSUPPORTED` | 2 | Requested behaviour is out of scope, for example an unknown romanisation. | Use one of the listed alternatives. |
| `APPROVAL_REQUIRED` | 3 | Action is destructive or security-sensitive and `--approve` was absent. | Re-run with `--approve` after reviewing the preview. |
| `NOT_FOUND` | 5 | A referenced file, directory or capability id does not exist. | Check the path relative to `--cwd`. |
| `TIMEOUT` | 6 | The capability exceeded its time budget. | Raise `--timeout` or reduce the input. |
| `LIMIT_EXCEEDED` | 7 | Input or output exceeded a hard limit. | Split the input; see section 12. |
| `SKILL_ERROR` | 4 | Any other validated failure. | Check `details`; run `selftest` if it reproduces. |

Exit code 0 always means success, so `&&` chaining is safe.

### 4.6 Parameters

Parameters are declared per capability in `asset-capability-catalog.json` and enforced before the handler runs: unknown names are rejected with a hint that lists the accepted ones, and a `did you mean` suggestion when the name is close. Types are `string`, `integer`, `number`, `boolean`, `enum` and `list`; enums are case-insensitive; lists accept `a,b` or a JSON array.

```bash
python3 tools-ultimate-text.py info emoji.emoji-sanitizer        # see mode/skin/format and defaults
python3 tools-ultimate-text.py run emoji.emoji-sanitizer --text '👍' --set mode=shortcode
python3 tools-ultimate-text.py run emoji.emoji-sanitizer --text '👍' --set mode=telepathy   # INVALID_PARAM
```

### 4.7 Configuration

Configuration merges three layers: built-in defaults, `asset-config-defaults.json`, then a file passed with `--config`, then per-invocation overrides such as `--overwrite`. Unknown keys are rejected so a typo cannot silently do nothing.

| Key | Default | Effect |
| --- | --- | --- |
| `workspace_root` | `.` | base directory for relative input/output paths. |
| `default_encoding` | `utf-8` | encoding used when none is given. |
| `encoding_errors` | `strict` | `strict` fails on undecodable bytes; `replace` keeps going. |
| `default_output_format` | `text` | default rendering for human output. |
| `style_profile` | `neutral-us` | named style profile used by style-aware capabilities. |
| `locale` | `en-US` | default locale for numerals, dates and casing. |
| `default_timeout_s` | `20` | per-capability time budget when the catalog has none. |
| `retries` | `0` | retries for transient failures inside a run. |
| `allow_overwrite` | `false` | permit `--out` to replace an existing file. |
| `approval_required_for_write` | `false` | also gate plain `--out` writes. |
| `max_input_bytes` | `4194304` | hard cap on input size. |
| `paths` | `{"input_dir": ".", "output_dir": "."}` | `input_dir` / `output_dir` used by automation capabilities that work on directories. |
| `max_pipeline_depth` | `4` | nesting limit for pipelines that call other capabilities. |
| `slow_capability_ms` | `2000` | warning threshold used by the performance capability. |

```bash
cp asset-config-defaults.json my-config.json     # edit, then:
python3 tools-ultimate-text.py run typography.typography-cleaner --text-file in.txt --config my-config.json
```

### 4.8 Timeouts, retries, cancellation and cleanup

- Every capability carries a `timeout_s` in the catalog; `--timeout` overrides it up to 300 s.
- Time limits are enforced with a signal alarm where available and a wall-clock check otherwise, so a run cannot hang the caller indefinitely.
- `Ctrl-C` exits with code 130 and no partial output file is left behind.
- `--out` writes through a temporary file in the same directory and renames it into place, so an interrupted write never leaves a half-written file.
- `retries` in the config re-attempts a capability that raised a transient error; deterministic failures are not retried.

---

## 5. Capability list (240 capabilities in 20 categories)

`engine=local` means the capability is implemented in Python here and runs offline. `engine=agent` means the capability emits a task contract for the calling assistant to produce the text, after which `validate` checks the result against the same acceptance checks. That split is recorded per capability in the catalog and shown by `list --engine`.

### 5.1 Category index

| # | Slug | Category | Local | Agent |
| --- | --- | --- | --- | --- |
| 1 | `creation` | Universal Text Creation and Transformation | 5 | 7 |
| 2 | `editing` | Editing, Proofreading, and Revision | 12 | 0 |
| 3 | `unicode` | Unicode, Characters, Symbols, and Encoding | 12 | 0 |
| 4 | `legacy-charsets` | ASCII, ANSI, Legacy Character Sets, and Shortcuts | 12 | 0 |
| 5 | `text-art` | Text Art, ASCII Art, and Typographic Design | 12 | 0 |
| 6 | `emoji` | Emoji, Pictographs, and Digital Expression | 12 | 0 |
| 7 | `languages` | Languages, Scripts, Transliteration, and Localization | 10 | 2 |
| 8 | `typography` | Typography, Punctuation, and Editorial Style | 12 | 0 |
| 9 | `structured-formats` | Structured Text, Markup, and Data Formats | 12 | 0 |
| 10 | `code-text` | Programming, Code Text, and Machine-Readable Content | 9 | 3 |
| 11 | `retrieval` | Search, Reference, and Text Knowledge Retrieval | 6 | 6 |
| 12 | `professional` | Document, Workplace, Academic, and Professional Text | 6 | 6 |
| 13 | `creative` | Creative Writing, Games, and Narrative Text | 8 | 4 |
| 14 | `analysis` | Text Analysis, Extraction, and Classification | 12 | 0 |
| 15 | `safety` | Accessibility, Safety, Privacy, and Inclusive Language | 11 | 1 |
| 16 | `integrity` | Text Integrity, Security, and Forensics | 12 | 0 |
| 17 | `media-text` | Speech, Captions, OCR, and Text Across Media | 12 | 0 |
| 18 | `automation` | Automation, Batch Processing, and Quality Assurance | 12 | 0 |
| 19 | `notation` | Specialized Symbolic and Technical Notation | 12 | 0 |
| 20 | `meta` | Meta-Skills for a Complete Text System | 12 | 0 |

### 5.2 Every capability

Each row gives the capability id, its engine, the parameters that matter most (with defaults) and a runnable example. `-` in the parameters column means the capability takes input only.

#### 1. Universal Text Creation and Transformation (`creation`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `creation.universal-text-composer` | agent | audience=general readers genre=article register=neutral length=medium max_words=0 must_include=[] must_avoid=[] | `python3 tools-ultimate-text.py run creation.universal-text-composer --text "<your text>"` |
| `creation.text-rewriter-and-reframer` | agent | target=clarity preserve_facts=true max_words=0 | `python3 tools-ultimate-text.py run creation.text-rewriter-and-reframer --text "<your text>"` |
| `creation.expansion-and-elaboration-engine` | agent | target_length=2x structure=prose detail_level=standard | `python3 tools-ultimate-text.py run creation.expansion-and-elaboration-engine --text "<your text>"` |
| `creation.compression-and-condensation-engine` | local | mode=executive max_words=120 max_chars=0 keep_headings=false sentences=5 | `python3 tools-ultimate-text.py run creation.compression-and-condensation-engine --text-file long-notes.md --set ratio=0.4` |
| `creation.format-to-format-text-converter` | local | to=list separator=auto key_name=item value_name=detail | `python3 tools-ultimate-text.py run creation.format-to-format-text-converter --text "<your text>"` |
| `creation.constraint-based-writing-solver` | local | exact_chars=0 max_chars=0 min_words=0 max_words=0 max_syllables=0 case=keep check_only=false | `python3 tools-ultimate-text.py run creation.constraint-based-writing-solver --text "<your text>"` |
| `creation.text-continuation-and-completion` | agent | unit=paragraph max_words=150 style_match=true | `python3 tools-ultimate-text.py run creation.text-continuation-and-completion --text "<your text>"` |
| `creation.variant-and-alternative-generator` | agent | dimension=title count=5 | `python3 tools-ultimate-text.py run creation.variant-and-alternative-generator --text "<your text>"` |
| `creation.tone-and-register-controller` | agent | target_register=formal intensity=3 preserve_facts=true | `python3 tools-ultimate-text.py run creation.tone-and-register-controller --text "<your text>"` |
| `creation.audience-adaptation-editor` | agent | audience=students keep_terms=[] | `python3 tools-ultimate-text.py run creation.audience-adaptation-editor --text "<your text>"` |
| `creation.plain-language-converter` | local | strength=standard max_sentence_words=22 keep_terms=[] list_changes=true profile=plain-en | `python3 tools-ultimate-text.py run creation.plain-language-converter --text "The majority of users utilise it." --set strength=aggressive` |
| `creation.text-quality-finisher` | local | mode=full profile=neutral-us max_sentence_words=28 | `python3 tools-ultimate-text.py run creation.text-quality-finisher --text-file draft.md --set mode=full --json` |

#### 2. Editing, Proofreading, and Revision (`editing`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `editing.grammar-and-mechanics-editor` | local | dialect=en-US apply=true report=true | `python3 tools-ultimate-text.py run editing.grammar-and-mechanics-editor --text "<your text>"` |
| `editing.developmental-editor` | local | focus=all format=markdown | `python3 tools-ultimate-text.py run editing.developmental-editor --text "<your text>"` |
| `editing.line-and-copy-editor` | local | intensity=2 apply=false report=true | `python3 tools-ultimate-text.py run editing.line-and-copy-editor --text "<your text>"` |
| `editing.structural-reorganization-editor` | local | mode=lead-first target_paragraph_words=90 | `python3 tools-ultimate-text.py run editing.structural-reorganization-editor --text "<your text>"` |
| `editing.consistency-and-terminology-auditor` | local | ignore_case=true format=table | `python3 tools-ultimate-text.py run editing.consistency-and-terminology-auditor --text "<your text>"` |
| `editing.ambiguity-and-logic-detector` | local | scope=all format=table | `python3 tools-ultimate-text.py run editing.ambiguity-and-logic-detector --text "<your text>"` |
| `editing.redundancy-and-fluff-trimmer` | local | strength=2 remove_repeated_words=true report=true | `python3 tools-ultimate-text.py run editing.redundancy-and-fluff-trimmer --text "<your text>"` |
| `editing.fact-claim-marker` | local | style=table kinds=[numbers,dates,superlatives,attribution] | `python3 tools-ultimate-text.py run editing.fact-claim-marker --text "<your text>"` |
| `editing.sensitive-tone-reviewer` | local | domain=general suggest=true format=table | `python3 tools-ultimate-text.py run editing.sensitive-tone-reviewer --text "<your text>"` |
| `editing.change-explanation-editor` | local | mode=editorial context_lines=1 | `python3 tools-ultimate-text.py run editing.change-explanation-editor --text "<your text>"` |
| `editing.style-sheet-builder` | local | name=house-style format=markdown | `python3 tools-ultimate-text.py run editing.style-sheet-builder --text "<your text>"` |
| `editing.version-merge-editor` | local | strategy=conflict-markers context_lines=2 | `python3 tools-ultimate-text.py run editing.version-merge-editor --text "<your text>"` |

#### 3. Unicode, Characters, Symbols, and Encoding (`unicode`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `unicode.unicode-character-explorer` | local | max_results=40 plane=any | `python3 tools-ultimate-text.py run unicode.unicode-character-explorer --text "<your text>"` |
| `unicode.character-identity-inspector` | local | format=table max_chars=200 | `python3 tools-ultimate-text.py run unicode.character-identity-inspector --text "é"` |
| `unicode.symbol-and-glyph-finder` | local | category=any max_results=40 | `python3 tools-ultimate-text.py run unicode.symbol-and-glyph-finder --text "<your text>"` |
| `unicode.combining-mark-composer` | local | marks=acute below=false combine_mode=nfc | `python3 tools-ultimate-text.py run unicode.combining-mark-composer --text "<your text>"` |
| `unicode.unicode-normalization-manager` | local | form=NFC report=true | `python3 tools-ultimate-text.py run unicode.unicode-normalization-manager --text "<your text>"` |
| `unicode.invisible-character-detector` | local | action=report format=table | `python3 tools-ultimate-text.py run unicode.invisible-character-detector --text-file suspect.txt` |
| `unicode.confusable-and-homoglyph-auditor` | local | mode=report format=table | `python3 tools-ultimate-text.py run unicode.confusable-and-homoglyph-auditor --text "<your text>"` |
| `unicode.bidirectional-text-specialist` | local | action=report base_dir=auto | `python3 tools-ultimate-text.py run unicode.bidirectional-text-specialist --text "<your text>"` |
| `unicode.private-use-character-advisor` | local | action=report replacement=� | `python3 tools-ultimate-text.py run unicode.private-use-character-advisor --text "<your text>"` |
| `unicode.unicode-escape-converter` | local | to=escape-u direction=encode encoding=utf-8 | `python3 tools-ultimate-text.py run unicode.unicode-escape-converter --text "<your text>"` |
| `unicode.font-coverage-planner` | local | scope=text format=markdown probe_system_fonts=false | `python3 tools-ultimate-text.py run unicode.font-coverage-planner --text "<your text>"` |
| `unicode.mojibake-repair-assistant` | local | source_encoding=auto apply=false max_candidates=6 | `python3 tools-ultimate-text.py run unicode.mojibake-repair-assistant --text "<your text>"` |

#### 4. ASCII, ANSI, Legacy Character Sets, and Shortcuts (`legacy-charsets`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `legacy-charsets.ascii-table-reference` | local | codepoints=32-127 format=table show_control_names=true | `python3 tools-ultimate-text.py run legacy-charsets.ascii-table-reference --text "<your text>"` |
| `legacy-charsets.extended-ascii-clarifier` | local | encoding=all | `python3 tools-ultimate-text.py run legacy-charsets.extended-ascii-clarifier --text "<your text>"` |
| `legacy-charsets.control-character-interpreter` | local | action=report show_hex=true | `python3 tools-ultimate-text.py run legacy-charsets.control-character-interpreter --text "<your text>"` |
| `legacy-charsets.alt-code-and-input-shortcut-guide` | local | platform=windows limit=40 | `python3 tools-ultimate-text.py run legacy-charsets.alt-code-and-input-shortcut-guide --text "<your text>"` |
| `legacy-charsets.keyboard-symbol-mapper` | local | platform=windows | `python3 tools-ultimate-text.py run legacy-charsets.keyboard-symbol-mapper --text "<your text>"` |
| `legacy-charsets.code-page-converter` | local | from_encoding=cp1252 to_encoding=utf-8 errors=strict direction=decode | `python3 tools-ultimate-text.py run legacy-charsets.code-page-converter --text "<your text>"` |
| `legacy-charsets.ebcdic-reference-assistant` | local | direction=decode codepage=cp037 | `python3 tools-ultimate-text.py run legacy-charsets.ebcdic-reference-assistant --text "<your text>"` |
| `legacy-charsets.terminal-escape-sequence-author` | local | action=wrap fg=red bg=default attrs=[] | `python3 tools-ultimate-text.py run legacy-charsets.terminal-escape-sequence-author --text "<your text>"` |
| `legacy-charsets.ascii-safe-transliterator` | local | mode=fallback fallback_char=? | `python3 tools-ultimate-text.py run legacy-charsets.ascii-safe-transliterator --text "<your text>"` |
| `legacy-charsets.newline-and-tab-normalizer` | local | newline=lf tabstop=4 trim_trailing=true ensure_final_newline=true | `python3 tools-ultimate-text.py run legacy-charsets.newline-and-tab-normalizer --text "<your text>"` |
| `legacy-charsets.byte-level-text-visualizer` | local | encoding=utf-8 width=16 max_bytes=512 show_ascii=true | `python3 tools-ultimate-text.py run legacy-charsets.byte-level-text-visualizer --text "<your text>"` |
| `legacy-charsets.character-table-fetch-planner` | local | standard=unicode format=markdown | `python3 tools-ultimate-text.py run legacy-charsets.character-table-fetch-planner --text "<your text>"` |

#### 5. Text Art, ASCII Art, and Typographic Design (`text-art`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `text-art.ascii-art-illustrator` | local | ramp= .:-=+*#%@ width=0 invert=false | `python3 tools-ultimate-text.py run text-art.ascii-art-illustrator --text "<your text>"` |
| `text-art.figlet-style-banner-maker` | local | font=standard width=80 spacing=1 | `python3 tools-ultimate-text.py run text-art.figlet-style-banner-maker --text "Release 2.0" --set font=block` |
| `text-art.unicode-text-art-designer` | local | style=blocks font=block width=80 | `python3 tools-ultimate-text.py run text-art.unicode-text-art-designer --text "<your text>"` |
| `text-art.box-and-border-builder` | local | style=single padding=1 align=left width=0 | `python3 tools-ultimate-text.py run text-art.box-and-border-builder --text "<your text>"` |
| `text-art.text-mosaic-and-halftone-maker` | local | ramp=░▒▓█ scale=1 invert=false char_mode=shade | `python3 tools-ultimate-text.py run text-art.text-mosaic-and-halftone-maker --text "<your text>"` |
| `text-art.kaomoji-composer` | local | mood=happy count=6 | `python3 tools-ultimate-text.py run text-art.kaomoji-composer --text "<your text>"` |
| `text-art.emoticon-and-text-reaction-maker` | local | mood=happy count=8 style=mixed | `python3 tools-ultimate-text.py run text-art.emoticon-and-text-reaction-maker --text "<your text>"` |
| `text-art.typographic-ornament-designer` | local | kind=divider width=40 count=3 | `python3 tools-ultimate-text.py run text-art.typographic-ornament-designer --text "<your text>"` |
| `text-art.text-logo-concept-generator` | local | count=4 style=mixed max_width=60 | `python3 tools-ultimate-text.py run text-art.text-logo-concept-generator --text "<your text>"` |
| `text-art.monospace-alignment-repairer` | local | mode=auto separator=\| align=left | `python3 tools-ultimate-text.py run text-art.monospace-alignment-repairer --text "<your text>"` |
| `text-art.text-pattern-and-tessellation-maker` | local | rows=5 cols=5 mode=tile | `python3 tools-ultimate-text.py run text-art.text-pattern-and-tessellation-maker --text "<your text>"` |
| `text-art.accessible-text-art-companion` | local | role=decorative max_chars=200 | `python3 tools-ultimate-text.py run text-art.accessible-text-art-companion --text "<your text>"` |

#### 6. Emoji, Pictographs, and Digital Expression (`emoji`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `emoji.emoji-meaning-and-usage-guide` | local | detail=full | `python3 tools-ultimate-text.py run emoji.emoji-meaning-and-usage-guide --text "<your text>"` |
| `emoji.emoji-sequence-composer` | local | base=thumbs up tone=0 zwj=[] variation=false | `python3 tools-ultimate-text.py run emoji.emoji-sequence-composer --text "<your text>"` |
| `emoji.emoji-translator` | local | direction=to-emoji density=medium | `python3 tools-ultimate-text.py run emoji.emoji-translator --text "<your text>"` |
| `emoji.emoji-accessibility-writer` | local | style=alt-text | `python3 tools-ultimate-text.py run emoji.emoji-accessibility-writer --text "<your text>"` |
| `emoji.emoji-compatibility-advisor` | local | target=oldest | `python3 tools-ultimate-text.py run emoji.emoji-compatibility-advisor --text "<your text>"` |
| `emoji.reaction-palette-designer` | local | mood=approval count=6 | `python3 tools-ultimate-text.py run emoji.reaction-palette-designer --text "<your text>"` |
| `emoji.sticker-and-caption-text-planner` | local | theme=launch count=6 max_chars=60 tone=playful | `python3 tools-ultimate-text.py run emoji.sticker-and-caption-text-planner --text "<your text>"` |
| `emoji.symbolic-moodboard-maker` | local | theme=calm focus count=12 | `python3 tools-ultimate-text.py run emoji.symbolic-moodboard-maker --text "<your text>"` |
| `emoji.emoji-search-by-concept` | local | limit=10 | `python3 tools-ultimate-text.py run emoji.emoji-search-by-concept --text "<your text>"` |
| `emoji.flag-sequence-interpreter` | local | format=table | `python3 tools-ultimate-text.py run emoji.flag-sequence-interpreter --text "🇵🇭 🇯🇵"` |
| `emoji.emoji-sanitizer` | local | mode=strip allow=[] | `python3 tools-ultimate-text.py run emoji.emoji-sanitizer --text "great 👍 👍🏽" --set mode=shortcode` |
| `emoji.textual-fallback-generator` | local | style=ascii max_chars=0 | `python3 tools-ultimate-text.py run emoji.textual-fallback-generator --text "<your text>"` |

#### 7. Languages, Scripts, Transliteration, and Localization (`languages`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `languages.multilingual-text-creator` | agent | languages=[Spanish] register=neutral keep_terms=[] | `python3 tools-ultimate-text.py run languages.multilingual-text-creator --text "<your text>"` |
| `languages.translation-and-localization-editor` | agent | target_locale=es-ES formality=neutral max_chars=0 keep_terms=[] | `python3 tools-ultimate-text.py run languages.translation-and-localization-editor --text "<your text>"` |
| `languages.transliteration-engine` | local | scheme=auto reverse=false | `python3 tools-ultimate-text.py run languages.transliteration-engine --text "Москва" --set scheme=auto` |
| `languages.romanization-system-guide` | local | script=cyrillic | `python3 tools-ultimate-text.py run languages.romanization-system-guide --text "<your text>"` |
| `languages.script-detection-and-segmentation` | local | unit=script | `python3 tools-ultimate-text.py run languages.script-detection-and-segmentation --text "<your text>"` |
| `languages.diacritic-and-accent-restorer` | local | language=auto style=precomposed | `python3 tools-ultimate-text.py run languages.diacritic-and-accent-restorer --text "<your text>"` |
| `languages.orthography-converter` | local | variant=us-gb | `python3 tools-ultimate-text.py run languages.orthography-converter --text "<your text>"` |
| `languages.right-to-left-layout-editor` | local | action=isolate base_dir=auto | `python3 tools-ultimate-text.py run languages.right-to-left-layout-editor --text "<your text>"` |
| `languages.input-method-helper` | local | platform=windows | `python3 tools-ultimate-text.py run languages.input-method-helper --text "<your text>"` |
| `languages.locale-aware-casing-specialist` | local | locale=en-US case=upper | `python3 tools-ultimate-text.py run languages.locale-aware-casing-specialist --text "istanbul" --set locale=tr-TR --set case=upper` |
| `languages.plural-and-inflection-template-builder` | local | format=icu locales=[en,fr,ar,ru] | `python3 tools-ultimate-text.py run languages.plural-and-inflection-template-builder --text "<your text>"` |
| `languages.language-learning-text-adapter` | local | level=A2 annotate=all | `python3 tools-ultimate-text.py run languages.language-learning-text-adapter --text "<your text>"` |

#### 8. Typography, Punctuation, and Editorial Style (`typography`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `typography.typography-cleaner` | local | profile=neutral-us fix_spacing=true smart_quotes=true ellipsis=true dashes=true | `python3 tools-ultimate-text.py run typography.typography-cleaner --text "He said ... and then--left."` |
| `typography.punctuation-style-converter` | local | serial_comma=true dash=em quotes=curly spacing=none | `python3 tools-ultimate-text.py run typography.punctuation-style-converter --text "<your text>"` |
| `typography.smart-quote-manager` | local | direction=to-curly language=en primes=true | `python3 tools-ultimate-text.py run typography.smart-quote-manager --text "<your text>"` |
| `typography.dash-and-hyphen-specialist` | local | dash=en ranges=true spaced=true | `python3 tools-ultimate-text.py run typography.dash-and-hyphen-specialist --text "<your text>"` |
| `typography.whitespace-and-line-break-designer` | local | width=72 nbsp=true prevent_widows=true hard_wrap=true | `python3 tools-ultimate-text.py run typography.whitespace-and-line-break-designer --text "<your text>"` |
| `typography.numeral-and-unit-formatter` | local | locale=en-US unit_space=nbsp numerals=auto group_digits=true | `python3 tools-ultimate-text.py run typography.numeral-and-unit-formatter --text "12500 units in 2024" --set locale=de-DE` |
| `typography.date-and-time-style-editor` | local | target=iso timezone=UTC include_time=true | `python3 tools-ultimate-text.py run typography.date-and-time-style-editor --text "<your text>"` |
| `typography.citation-style-text-formatter` | local | style=apa source_type=journal | `python3 tools-ultimate-text.py run typography.citation-style-text-formatter --text "<your text>"` |
| `typography.headline-and-title-case-editor` | local | style=title-ap | `python3 tools-ultimate-text.py run typography.headline-and-title-case-editor --text "<your text>"` |
| `typography.widow-orphan-and-rag-helper` | local | width=60 min_words_last_line=3 mode=prevent | `python3 tools-ultimate-text.py run typography.widow-orphan-and-rag-helper --text "<your text>"` |
| `typography.readability-and-rhythm-analyzer` | local | metrics=[flesch,fk,fog,smog,ari,coleman-liau] language=en sample_sentences=3 | `python3 tools-ultimate-text.py run typography.readability-and-rhythm-analyzer --text "<your text>"` |
| `typography.house-style-enforcer` | local | profile=neutral-us apply=true report=true | `python3 tools-ultimate-text.py run typography.house-style-enforcer --text "<your text>"` |

#### 9. Structured Text, Markup, and Data Formats (`structured-formats`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `structured-formats.markdown-author-and-repairer` | local | action=lint max_line=0 fix=true | `python3 tools-ultimate-text.py run structured-formats.markdown-author-and-repairer --text "<your text>"` |
| `structured-formats.html-text-and-entity-editor` | local | action=to-text keep_links=false keep_lists=false | `python3 tools-ultimate-text.py run structured-formats.html-text-and-entity-editor --text "<your text>"` |
| `structured-formats.xml-and-sgml-text-specialist` | local | action=pretty indent=2 | `python3 tools-ultimate-text.py run structured-formats.xml-and-sgml-text-specialist --text "<your text>"` |
| `structured-formats.json-string-and-jsonl-editor` | local | action=validate indent=2 | `python3 tools-ultimate-text.py run structured-formats.json-string-and-jsonl-editor --text "<your text>"` |
| `structured-formats.yaml-text-safety-editor` | local | action=lint indent=2 | `python3 tools-ultimate-text.py run structured-formats.yaml-text-safety-editor --text "<your text>"` |
| `structured-formats.csv-and-delimited-text-specialist` | local | action=table delimiter=auto quotechar=" to_delimiter=	 has_header=true | `python3 tools-ultimate-text.py run structured-formats.csv-and-delimited-text-specialist --text "<your text>"` |
| `structured-formats.regular-expression-text-toolkit` | local | action=search flags=i max_matches=200 group=0 | `python3 tools-ultimate-text.py run structured-formats.regular-expression-text-toolkit --text-file log.txt --set pattern=ERROR` |
| `structured-formats.template-language-text-builder` | local | action=render context={} strict=true | `python3 tools-ultimate-text.py run structured-formats.template-language-text-builder --text "<your text>"` |
| `structured-formats.diff-and-patch-text-creator` | local | mode=unified context_lines=3 | `python3 tools-ultimate-text.py run structured-formats.diff-and-patch-text-creator --text "<your text>"` |
| `structured-formats.schema-to-text-generator` | local | format=markdown | `python3 tools-ultimate-text.py run structured-formats.schema-to-text-generator --text "<your text>"` |
| `structured-formats.frontmatter-and-metadata-editor` | local | action=read format=yaml | `python3 tools-ultimate-text.py run structured-formats.frontmatter-and-metadata-editor --text "<your text>"` |
| `structured-formats.structured-text-round-trip-guardian` | local | to=json strict=true | `python3 tools-ultimate-text.py run structured-formats.structured-text-round-trip-guardian --text "<your text>"` |

#### 10. Programming, Code Text, and Machine-Readable Content (`code-text`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `code-text.code-generator-and-editor` | agent | language=python tests=false | `python3 tools-ultimate-text.py run code-text.code-generator-and-editor --text "<your text>"` |
| `code-text.code-comment-and-documentation-writer` | local | language=python style=google action=scaffold | `python3 tools-ultimate-text.py run code-text.code-comment-and-documentation-writer --text "<your text>"` |
| `code-text.identifier-naming-assistant` | local | case=snake language=generic | `python3 tools-ultimate-text.py run code-text.identifier-naming-assistant --text "<your text>"` |
| `code-text.string-literal-escaper` | local | language=python direction=escape quote=double | `python3 tools-ultimate-text.py run code-text.string-literal-escaper --text "<your text>"` |
| `code-text.prompt-and-system-instruction-editor` | local | action=lint target=system max_words=0 | `python3 tools-ultimate-text.py run code-text.prompt-and-system-instruction-editor --text "<your text>"` |
| `code-text.domain-specific-language-author` | local | action=scaffold start=document | `python3 tools-ultimate-text.py run code-text.domain-specific-language-author --text "<your text>"` |
| `code-text.pseudo-code-and-algorithm-narrator` | agent | style=structured audience=developers | `python3 tools-ultimate-text.py run code-text.pseudo-code-and-algorithm-narrator --text "<your text>"` |
| `code-text.code-to-prose-translator` | agent | audience=developers max_words=200 | `python3 tools-ultimate-text.py run code-text.code-to-prose-translator --text "<your text>"` |
| `code-text.prose-to-code-specification-builder` | local | language=python format=spec-md include_tests=true | `python3 tools-ultimate-text.py run code-text.prose-to-code-specification-builder --text "<your text>"` |
| `code-text.command-line-text-composer` **(approval)** | local | action=compose shell=bash args=[] dry_run=true | `python3 tools-ultimate-text.py run code-text.command-line-text-composer --text "<your text>"` |
| `code-text.test-data-and-fixture-text-maker` | local | kind=names count=5 seed=1 | `python3 tools-ultimate-text.py run code-text.test-data-and-fixture-text-maker --text "<your text>"` |
| `code-text.machine-output-formatter` | local | format=auto action=detect | `python3 tools-ultimate-text.py run code-text.machine-output-formatter --text "<your text>"` |

#### 11. Search, Reference, and Text Knowledge Retrieval (`retrieval`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `retrieval.character-and-symbol-researcher` | agent | depth=standard cite=true | `python3 tools-ultimate-text.py run retrieval.character-and-symbol-researcher --text "<your text>"` |
| `retrieval.living-standard-update-checker` | agent | standard=unicode format=markdown | `python3 tools-ultimate-text.py run retrieval.living-standard-update-checker --text "<your text>"` |
| `retrieval.source-grounded-text-synthesizer` | agent | sources=[] style=report max_words=400 | `python3 tools-ultimate-text.py run retrieval.source-grounded-text-synthesizer --text "<your text>"` |
| `retrieval.terminology-researcher` | agent | languages=[en] | `python3 tools-ultimate-text.py run retrieval.terminology-researcher --text "<your text>"` |
| `retrieval.quotation-and-attribution-verifier` | agent | strictness=standard | `python3 tools-ultimate-text.py run retrieval.quotation-and-attribution-verifier --text "<your text>"` |
| `retrieval.cross-language-reference-finder` | agent | languages=[en,fr] | `python3 tools-ultimate-text.py run retrieval.cross-language-reference-finder --text "<your text>"` |
| `retrieval.text-standard-navigator` | local | area=any format=markdown | `python3 tools-ultimate-text.py run retrieval.text-standard-navigator --text "<your text>"` |
| `retrieval.shortcut-documentation-retriever` | local | platform=any format=table | `python3 tools-ultimate-text.py run retrieval.shortcut-documentation-retriever --text "<your text>"` |
| `retrieval.corpus-example-finder` | local | context=1 regex=false max_hits=20 | `python3 tools-ultimate-text.py run retrieval.corpus-example-finder --text "<your text>"` |
| `retrieval.source-conflict-resolver` | local | mode=table | `python3 tools-ultimate-text.py run retrieval.source-conflict-resolver --text "<your text>"` |
| `retrieval.citation-ready-research-note-maker` | local | style=apa | `python3 tools-ultimate-text.py run retrieval.citation-ready-research-note-maker --text "<your text>"` |
| `retrieval.reference-table-builder` | local | format=markdown | `python3 tools-ultimate-text.py run retrieval.reference-table-builder --text "<your text>"` |

#### 12. Document, Workplace, Academic, and Professional Text (`professional`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `professional.email-and-message-writer` | agent | kind=email tone=warm-professional length=short | `python3 tools-ultimate-text.py run professional.email-and-message-writer --text "<your text>"` |
| `professional.report-and-brief-writer` | agent | kind=brief sections=[summary,findings,risks,recommendation] length=1-page | `python3 tools-ultimate-text.py run professional.report-and-brief-writer --text "<your text>"` |
| `professional.meeting-text-assistant` | local | action=all format=markdown | `python3 tools-ultimate-text.py run professional.meeting-text-assistant --text-file transcript.txt --set action=all` |
| `professional.policy-and-procedure-writer` | agent | kind=procedure framework=plain audience=staff | `python3 tools-ultimate-text.py run professional.policy-and-procedure-writer --text "<your text>"` |
| `professional.business-template-builder` | local | template=status-update | `python3 tools-ultimate-text.py run professional.business-template-builder --text "<your text>"` |
| `professional.academic-writing-editor` | agent | section=abstract style=apa | `python3 tools-ultimate-text.py run professional.academic-writing-editor --text "<your text>"` |
| `professional.learning-material-creator` | local | kind=all count=8 level=intro | `python3 tools-ultimate-text.py run professional.learning-material-creator --text "<your text>"` |
| `professional.resume-and-application-text-editor` | local | target=resume style=bullets max_bullet_words=24 | `python3 tools-ultimate-text.py run professional.resume-and-application-text-editor --text "<your text>"` |
| `professional.customer-support-response-maker` | local | intent=auto tone=warm max_words=160 | `python3 tools-ultimate-text.py run professional.customer-support-response-maker --text "<your text>"` |
| `professional.marketing-copy-studio` | agent | channel=landing-page framework=pas count=3 | `python3 tools-ultimate-text.py run professional.marketing-copy-studio --text "<your text>"` |
| `professional.legal-style-plain-language-assistant` | local | strength=standard keep_defined_terms=true flag_obligations=true | `python3 tools-ultimate-text.py run professional.legal-style-plain-language-assistant --text "<your text>"` |
| `professional.scientific-and-technical-writer` | agent | genre=abstract max_words=300 | `python3 tools-ultimate-text.py run professional.scientific-and-technical-writer --text "<your text>"` |

#### 13. Creative Writing, Games, and Narrative Text (`creative`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `creative.story-and-fiction-composer` | agent | genre=literary pov=third-limited length=short max_words=800 | `python3 tools-ultimate-text.py run creative.story-and-fiction-composer --text "<your text>"` |
| `creative.poetry-and-verse-maker` | local | form=haiku count=1 seed=verse | `python3 tools-ultimate-text.py run creative.poetry-and-verse-maker --text "<your text>"` |
| `creative.dialogue-and-script-writer` | local | format=screenplay action=format | `python3 tools-ultimate-text.py run creative.dialogue-and-script-writer --text "<your text>"` |
| `creative.worldbuilding-text-architect` | agent | scope=world depth=standard | `python3 tools-ultimate-text.py run creative.worldbuilding-text-architect --text "<your text>"` |
| `creative.character-voice-designer` | agent | traits=[] register=plain samples=3 | `python3 tools-ultimate-text.py run creative.character-voice-designer --text "<your text>"` |
| `creative.interactive-fiction-writer` | local | action=scaffold nodes=6 seed=if | `python3 tools-ultimate-text.py run creative.interactive-fiction-writer --text "<your text>"` |
| `creative.riddle-and-wordplay-maker` | local | kind=all count=5 seed=wordplay | `python3 tools-ultimate-text.py run creative.riddle-and-wordplay-maker --text "<your text>"` |
| `creative.naming-and-neologism-studio` | local | style=fantasy count=12 seed=names max_len=14 | `python3 tools-ultimate-text.py run creative.naming-and-neologism-studio --text "<your text>"` |
| `creative.lore-and-continuity-editor` | local | checks=[names,dates,numbers,titles] format=table | `python3 tools-ultimate-text.py run creative.lore-and-continuity-editor --text "<your text>"` |
| `creative.roleplaying-text-generator` | local | kind=all count=4 seed=rp | `python3 tools-ultimate-text.py run creative.roleplaying-text-generator --text "<your text>"` |
| `creative.lyrics-and-chant-designer` | local | scheme=AABB lines=4 count=1 seed=lyrics | `python3 tools-ultimate-text.py run creative.lyrics-and-chant-designer --text "<your text>"` |
| `creative.creative-remix-planner` | agent | elements=[genre,setting,voice] count=5 | `python3 tools-ultimate-text.py run creative.creative-remix-planner --text "<your text>"` |

#### 14. Text Analysis, Extraction, and Classification (`analysis`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `analysis.key-information-extractor` | local | kinds=[emails,urls,phones,dates,money,percentages,hashtags,ids] format=table | `python3 tools-ultimate-text.py run analysis.key-information-extractor --text "Contact ana@example.com by 2024-05-06" --set format=json` |
| `analysis.topic-and-theme-analyzer` | local | top_n=10 method=rake | `python3 tools-ultimate-text.py run analysis.topic-and-theme-analyzer --text-file article.txt --set method=rake --set top_n=8` |
| `analysis.sentiment-and-tone-analyzer` | local | granularity=document | `python3 tools-ultimate-text.py run analysis.sentiment-and-tone-analyzer --text "<your text>"` |
| `analysis.argument-mapper` | local | format=outline | `python3 tools-ultimate-text.py run analysis.argument-mapper --text "<your text>"` |
| `analysis.document-structure-analyzer` | local | format=outline max_depth=4 | `python3 tools-ultimate-text.py run analysis.document-structure-analyzer --text "<your text>"` |
| `analysis.terminology-and-concordance-builder` | local | window=5 format=table min_count=3 | `python3 tools-ultimate-text.py run analysis.terminology-and-concordance-builder --text "<your text>"` |
| `analysis.similarity-and-duplicate-detector` | local | threshold=0.6 unit=line ignore_case=true | `python3 tools-ultimate-text.py run analysis.similarity-and-duplicate-detector --text "<your text>"` |
| `analysis.text-classification-designer` | local | method=keyword | `python3 tools-ultimate-text.py run analysis.text-classification-designer --text "<your text>"` |
| `analysis.requirement-and-obligation-extractor` | local | kinds=[must,should,may,prohibited] format=table | `python3 tools-ultimate-text.py run analysis.requirement-and-obligation-extractor --text "<your text>"` |
| `analysis.timeline-and-event-extractor` | local | format=table sort=true | `python3 tools-ultimate-text.py run analysis.timeline-and-event-extractor --text "<your text>"` |
| `analysis.question-and-answer-miner` | local | format=markdown max_pairs=50 | `python3 tools-ultimate-text.py run analysis.question-and-answer-miner --text "<your text>"` |
| `analysis.text-dataset-annotation-assistant` | local | kinds=[emails,urls,phone,dates,money,people,orgs] format=jsonl | `python3 tools-ultimate-text.py run analysis.text-dataset-annotation-assistant --text "<your text>"` |

#### 15. Accessibility, Safety, Privacy, and Inclusive Language (`safety`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `safety.accessible-language-editor` | local | level=plain expand_numbers=true max_changes=50 | `python3 tools-ultimate-text.py run safety.accessible-language-editor --text "<your text>"` |
| `safety.alternative-text-writer` | local | kind=image style=standard max_chars=200 | `python3 tools-ultimate-text.py run safety.alternative-text-writer --text "<your text>"` |
| `safety.inclusive-language-reviewer` | local | severity=low apply=false | `python3 tools-ultimate-text.py run safety.inclusive-language-reviewer --text "<your text>"` |
| `safety.trauma-informed-text-editor` | local | audience=general | `python3 tools-ultimate-text.py run safety.trauma-informed-text-editor --text "<your text>"` |
| `safety.privacy-and-pii-redactor` **(approval)** | local | mode=label types=all | `python3 tools-ultimate-text.py run safety.privacy-and-pii-redactor --text "write to ana@example.com" --set mode=token --approve` |
| `safety.secret-and-credential-scanner` | local | format=table | `python3 tools-ultimate-text.py run safety.secret-and-credential-scanner --text-file .env.example --approve` |
| `safety.content-warning-composer` | local | style=short placement=top | `python3 tools-ultimate-text.py run safety.content-warning-composer --text "<your text>"` |
| `safety.safe-placeholder-generator` | local | style=bracketed types=all | `python3 tools-ultimate-text.py run safety.safe-placeholder-generator --text "<your text>"` |
| `safety.harassment-and-abuse-de-escalation-writer` | agent | channel=reply tone=firm-warm | `python3 tools-ultimate-text.py run safety.harassment-and-abuse-de-escalation-writer --text "<your text>"` |
| `safety.high-stakes-disclaimer-editor` | local | domain=general action=insert placement=bottom | `python3 tools-ultimate-text.py run safety.high-stakes-disclaimer-editor --text "<your text>"` |
| `safety.bias-aware-dataset-text-reviewer` | local | dimension=any | `python3 tools-ultimate-text.py run safety.bias-aware-dataset-text-reviewer --text "<your text>"` |
| `safety.read-aloud-and-screen-reader-optimizer` | local | mode=spoken | `python3 tools-ultimate-text.py run safety.read-aloud-and-screen-reader-optimizer --text "<your text>"` |

#### 16. Text Integrity, Security, and Forensics (`integrity`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `integrity.encoding-forensics-analyst` | local | format=markdown | `python3 tools-ultimate-text.py run integrity.encoding-forensics-analyst --text "<your text>"` |
| `integrity.text-corruption-repairer` **(approval)** | local | dry_run=false | `python3 tools-ultimate-text.py run integrity.text-corruption-repairer --text-file broken.txt --approve` |
| `integrity.prompt-injection-and-untrusted-text-marker` | local | action=detect mode=annotate format=table | `python3 tools-ultimate-text.py run integrity.prompt-injection-and-untrusted-text-marker --text "<your text>"` |
| `integrity.bidi-spoofing-detector` | local | format=markdown | `python3 tools-ultimate-text.py run integrity.bidi-spoofing-detector --text "<your text>"` |
| `integrity.homoglyph-security-scanner` | local | policy=strict | `python3 tools-ultimate-text.py run integrity.homoglyph-security-scanner --text "<your text>"` |
| `integrity.steganographic-text-pattern-checker` | local | format=markdown | `python3 tools-ultimate-text.py run integrity.steganographic-text-pattern-checker --text "<your text>"` |
| `integrity.canonicalization-advisor` | local | target=nfc lower=false | `python3 tools-ultimate-text.py run integrity.canonicalization-advisor --text "<your text>"` |
| `integrity.checksum-and-text-fingerprint-helper` | local | algorithms=sha256 format=manifest | `python3 tools-ultimate-text.py run integrity.checksum-and-text-fingerprint-helper --text-file final.txt --set algorithms=sha256,md5` |
| `integrity.redaction-verification-assistant` | local | targets=email,phone_intl,credit_card mode=strict | `python3 tools-ultimate-text.py run integrity.redaction-verification-assistant --text "<your text>"` |
| `integrity.log-sanitization-editor` **(approval)** | local | mode=standard | `python3 tools-ultimate-text.py run integrity.log-sanitization-editor --text "<your text>"` |
| `integrity.text-provenance-labeler` | local | style=footer | `python3 tools-ultimate-text.py run integrity.text-provenance-labeler --text "<your text>"` |
| `integrity.lossless-transformation-planner` | local | goals=normalize-newlines,strip-trailing-space format=markdown | `python3 tools-ultimate-text.py run integrity.lossless-transformation-planner --text "<your text>"` |

#### 17. Speech, Captions, OCR, and Text Across Media (`media-text`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `media-text.transcript-cleaner` | local | fillers=true timestamps=true punctuation=true max_line_chars=0 keep_speakers=true | `python3 tools-ultimate-text.py run media-text.transcript-cleaner --text "<your text>"` |
| `media-text.speaker-diarization-text-editor` | local | action=normalize label_style=keep | `python3 tools-ultimate-text.py run media-text.speaker-diarization-text-editor --text "<your text>"` |
| `media-text.caption-and-subtitle-writer` | local | format=srt max_chars=42 max_lines=2 cps=17 start_ms=0 | `python3 tools-ultimate-text.py run media-text.caption-and-subtitle-writer --text-file script.txt --set format=srt --set max_chars=42` |
| `media-text.subtitle-localization-editor` | local | offset_ms=0 max_chars=42 cps=17 format=srt | `python3 tools-ultimate-text.py run media-text.subtitle-localization-editor --text "<your text>"` |
| `media-text.ocr-cleanup-specialist` | local | level=2 join_hyphenation=true fix_ligatures=true | `python3 tools-ultimate-text.py run media-text.ocr-cleanup-specialist --text "<your text>"` |
| `media-text.scanned-table-text-reconstructor` | local | delimiter=auto format=ascii | `python3 tools-ultimate-text.py run media-text.scanned-table-text-reconstructor --text "<your text>"` |
| `media-text.audio-script-and-pronunciation-writer` | local | respell=true pace=normal ssml=true | `python3 tools-ultimate-text.py run media-text.audio-script-and-pronunciation-writer --text "<your text>"` |
| `media-text.teleprompter-text-formatter` | local | width=38 chunk=3 markers=true | `python3 tools-ultimate-text.py run media-text.teleprompter-text-formatter --text "<your text>"` |
| `media-text.text-to-speech-preparation-editor` | local | expand=all ssml=true | `python3 tools-ultimate-text.py run media-text.text-to-speech-preparation-editor --text "<your text>"` |
| `media-text.video-chapter-and-summary-writer` | local | min_chapter_s=30 format=markdown | `python3 tools-ultimate-text.py run media-text.video-chapter-and-summary-writer --text "<your text>"` |
| `media-text.on-screen-text-planner` | local | platform=youtube duration_s=60 count=6 | `python3 tools-ultimate-text.py run media-text.on-screen-text-planner --text "<your text>"` |
| `media-text.multimodal-text-consistency-checker` | local | checks=[numbers,names,timing,titles] format=markdown | `python3 tools-ultimate-text.py run media-text.multimodal-text-consistency-checker --text "<your text>"` |

#### 18. Automation, Batch Processing, and Quality Assurance (`automation`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `automation.bulk-find-and-replace-planner` **(approval)** | local | glob=*.txt recursive=false regex=false case_sensitive=true max_files=50 write=false | `python3 tools-ultimate-text.py run automation.bulk-find-and-replace-planner --text "<your text>"` |
| `automation.batch-rename-text-generator` **(approval)** | local | glob=* recursive=false max_files=50 start=1 digits=2 | `python3 tools-ultimate-text.py run automation.batch-rename-text-generator --text "<your text>"` |
| `automation.text-pipeline-architect` | local | stop_on_error=true format=text | `python3 tools-ultimate-text.py run automation.text-pipeline-architect --text-file draft.txt --set "pipeline=typography.typography-cleaner\|creation.text-quality-finisher"` |
| `automation.text-linter-rule-designer` | local | action=run format=table | `python3 tools-ultimate-text.py run automation.text-linter-rule-designer --text "<your text>"` |
| `automation.golden-sample-and-regression-builder` **(approval)** | local | action=create format=markdown | `python3 tools-ultimate-text.py run automation.golden-sample-and-regression-builder --text "<your text>"` |
| `automation.edge-case-text-corpus-generator` | local | kinds=[empty,whitespace,bidi,combining,emoji,very-long,control,mixed-script,rtl,surrogates] count=3 seed=1 | `python3 tools-ultimate-text.py run automation.edge-case-text-corpus-generator --text "<your text>"` |
| `automation.text-conversion-qa-auditor` | local | formats=[json,csv,yaml,markdown,srt] samples=10 seed=1 | `python3 tools-ultimate-text.py run automation.text-conversion-qa-auditor --text "<your text>"` |
| `automation.deterministic-output-controller` | local | seed=1 normalize=[newlines,trailing-space,unicode] repeat=2 sort_lines=false | `python3 tools-ultimate-text.py run automation.deterministic-output-controller --text "<your text>"` |
| `automation.human-review-queue-builder` | local | threshold=0.75 format=jsonl | `python3 tools-ultimate-text.py run automation.human-review-queue-builder --text "<your text>"` |
| `automation.text-workflow-performance-optimizer` | local | repeat=3 input_bytes=2000 | `python3 tools-ultimate-text.py run automation.text-workflow-performance-optimizer --text "<your text>"` |
| `automation.text-tool-interoperability-adapter` | local | to=json from_format=auto | `python3 tools-ultimate-text.py run automation.text-tool-interoperability-adapter --text "<your text>"` |
| `automation.publication-readiness-gate` | local | checks=[lint,readability,pii,secrets,unicode,links,structure] format=markdown strict=false | `python3 tools-ultimate-text.py run automation.publication-readiness-gate --text-file final.md --set "checks=[lint,pii,secrets]" --json` |

#### 19. Specialized Symbolic and Technical Notation (`notation`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `notation.mathematical-notation-editor` | local | direction=to-unicode style=spaced | `python3 tools-ultimate-text.py run notation.mathematical-notation-editor --text "\alpha + \beta^2" --set direction=to-unicode` |
| `notation.scientific-symbol-specialist` | local | action=lookup direction=to-unicode | `python3 tools-ultimate-text.py run notation.scientific-symbol-specialist --text "<your text>"` |
| `notation.logic-and-set-notation-composer` | local | action=convert direction=to-unicode | `python3 tools-ultimate-text.py run notation.logic-and-set-notation-composer --text "<your text>"` |
| `notation.phonetic-alphabet-and-ipa-editor` | local | action=to-ipa scheme=xsampa | `python3 tools-ultimate-text.py run notation.phonetic-alphabet-and-ipa-editor --text "<your text>"` |
| `notation.music-symbol-and-chord-text-editor` | local | action=format key=C semitones=0 | `python3 tools-ultimate-text.py run notation.music-symbol-and-chord-text-editor --text "<your text>"` |
| `notation.chess-and-game-notation-editor` | local | action=validate | `python3 tools-ultimate-text.py run notation.chess-and-game-notation-editor --text "<your text>"` |
| `notation.braille-pattern-text-specialist` | local | action=encode six_dot=true | `python3 tools-ultimate-text.py run notation.braille-pattern-text-specialist --text "<your text>"` |
| `notation.box-drawing-and-diagram-notation-expert` | local | kind=tree style=unicode | `python3 tools-ultimate-text.py run notation.box-drawing-and-diagram-notation-expert --text "<your text>"` |
| `notation.currency-and-financial-symbol-guide` | local | format=table | `python3 tools-ultimate-text.py run notation.currency-and-financial-symbol-guide --text "<your text>"` |
| `notation.legal-and-editorial-markup-symbol-guide` | local | format=table | `python3 tools-ultimate-text.py run notation.legal-and-editorial-markup-symbol-guide --text "<your text>"` |
| `notation.technical-arrow-and-relation-selector` | local | direction=to-unicode style=all | `python3 tools-ultimate-text.py run notation.technical-arrow-and-relation-selector --text "<your text>"` |
| `notation.symbol-pronunciation-and-naming-guide` | local | format=table | `python3 tools-ultimate-text.py run notation.symbol-pronunciation-and-naming-guide --text "<your text>"` |

#### 20. Meta-Skills for a Complete Text System (`meta`)

| Capability | Engine | Key parameters | Example |
| --- | --- | --- | --- |
| `meta.text-task-router` | local | top_n=3 format=table | `python3 tools-ultimate-text.py run meta.text-task-router --set "mission=clean up video captions" --set top_n=3` |
| `meta.text-requirement-elicitor` | local | max_questions=8 format=markdown | `python3 tools-ultimate-text.py run meta.text-requirement-elicitor --text "<your text>"` |
| `meta.text-output-contract-builder` | local | format=json include_checks=true | `python3 tools-ultimate-text.py run meta.text-output-contract-builder --text "<your text>"` |
| `meta.text-tool-selection-advisor` | local | top_n=5 explain=true format=table | `python3 tools-ultimate-text.py run meta.text-tool-selection-advisor --text "<your text>"` |
| `meta.text-verification-orchestrator` | local | checks=[unicode,pii,secrets,readability,lint,length] format=markdown | `python3 tools-ultimate-text.py run meta.text-verification-orchestrator --text "<your text>"` |
| `meta.text-repair-and-recovery-manager` | local | strategy=auto max_steps=4 | `python3 tools-ultimate-text.py run meta.text-repair-and-recovery-manager --text "<your text>"` |
| `meta.text-capability-composer` | local | max_steps=5 format=markdown | `python3 tools-ultimate-text.py run meta.text-capability-composer --text "<your text>"` |
| `meta.text-preference-profiler` | local | name=profile format=json | `python3 tools-ultimate-text.py run meta.text-preference-profiler --text "<your text>"` |
| `meta.text-knowledge-base-curator` | local | action=index dir_path=. glob=*.* top_k=5 max_files=100 | `python3 tools-ultimate-text.py run meta.text-knowledge-base-curator --text "<your text>"` |
| `meta.text-skill-evaluation-designer` | local | cases=8 format=markdown | `python3 tools-ultimate-text.py run meta.text-skill-evaluation-designer --text "<your text>"` |
| `meta.text-skill-catalog-maintainer` | local | action=validate format=markdown | `python3 tools-ultimate-text.py run meta.text-skill-catalog-maintainer` |
| `meta.end-to-end-text-production-director` **(approval)** | local | gate=true format=markdown | `python3 tools-ultimate-text.py run meta.end-to-end-text-production-director --text "<your text>"` |

---

## 6. Worked examples

The outputs below are trimmed from real runs on this build; the selftest reproduces the same behaviour from fixtures, so you can confirm them locally.

### Fix typography in one line

```bash
python3 tools-ultimate-text.py run typography.typography-cleaner --text 'He said "hello"... and then--quietly--left.   Done.'
```

Result: He said “hello”… and then—quietly—left. Done.

### Locale-aware numerals keep years intact

```bash
python3 tools-ultimate-text.py run typography.numeral-and-unit-formatter --text 'We shipped 12500 units in 2024 and grew 3.5 %.' --set locale=de-DE
```

Result: We shipped 12.500 units in 2024 and grew 3.5 %.

### Start from a draft file and write the result

```bash
python3 tools-ultimate-text.py run creation.plain-language-converter --text-file draft.txt --set strength=aggressive --out cleaned.txt
```

Result: Writes cleaned.txt atomically; refuses to overwrite without --overwrite.

### Structured JSON for a pipeline

```bash
python3 tools-ultimate-text.py run analysis.key-information-extractor --text 'Contact ana@example.com about 12,500 USD by 2024-05-06.' --set format=json --json
```

Result: {"entities": {"emails": ["ana@example.com"], "money": ["12,500 USD"], "dates": ["2024-05-06"]}, "counts": {...}}

### Redact personal data (gated)

```bash
python3 tools-ultimate-text.py run safety.privacy-and-pii-redactor --text 'Mail ana@example.com or call +63 917 555 1234.' --set mode=token --approve
```

Result: Mail <EMAIL> or call <PHONE> ... plus a redaction summary table.

### Find a leaked credential (gated)

```bash
python3 tools-ultimate-text.py run safety.secret-and-credential-scanner --text 'key AKIAABCDEFGHIJKLMNOP' --approve
```

Result: Table with type aws_access_key_id, severity critical, and a redacted preview.

### Repair mojibake and normalise newlines

```bash
python3 tools-ultimate-text.py run integrity.text-corruption-repairer --text-file broken.txt --approve
```

Result: Returns readable text plus the repair steps and a confidence level.

### Captions from a script

```bash
python3 tools-ultimate-text.py run media-text.caption-and-subtitle-writer --text 'The system shipped on Monday. It reduced errors.' --set format=srt --set max_chars=32
```

Result: Numbered SRT cues with --> timings and a warning that timings are estimated.

### Chain capabilities in one call

```bash
python3 tools-ultimate-text.py run automation.text-pipeline-architect --text-file draft.txt --set 'pipeline=typography.typography-cleaner|creation.text-quality-finisher' --set format=json
```

Result: Per-step status, runtime and the final text.

### Check whether a document is publishable

```bash
python3 tools-ultimate-text.py run automation.publication-readiness-gate --text-file final.md --set 'checks=[lint,pii,secrets,unicode,structure]' --json
```

Result: verdict READY / READY WITH WARNINGS / BLOCKED with per-check detail.

### Ask the tool what it has for a job

```bash
python3 tools-ultimate-text.py run meta.text-task-router --set 'mission=clean up captions for a video' --set top_n=3
```

Result: Ranked capability ids with a suggested command per match.

### Agent-assisted capability: contract, then validate

```bash
python3 tools-ultimate-text.py contract creative.story-and-fiction-composer --format markdown
python3 tools-ultimate-text.py validate creative.story-and-fiction-composer --text-file story.md
```

Result: The contract lists goal, inputs, output schema and acceptance checks; validate scores the draft against them.

---

## 7. Safety, approval and privacy rules

### 7.1 Actions that need `--approve`

These capabilities - and any file write from a capability flagged in the catalog - refuse to run without an explicit `--approve`. The refusal is a structured `APPROVAL_REQUIRED` error (exit code 3) that states exactly what would have happened:

| Capability | Why it is gated |
| --- | --- |
| `code-text.command-line-text-composer` | builds a command line that is meant to be pasted into a shell; the preview is the safety net. |
| `safety.inclusive-language-reviewer` | can rewrite terminology in the document when `--set apply=true`. |
| `safety.privacy-and-pii-redactor` | removing personal data is irreversible in the written output. |
| `safety.secret-and-credential-scanner` | reads and reports credentials; the report itself is sensitive material. |
| `integrity.text-corruption-repairer` | repair rewrites damaged text, where the original may be the only copy. |
| `integrity.prompt-injection-and-untrusted-text-marker` | annotates untrusted input that later decisions may rely on. |
| `integrity.log-sanitization-editor` | sanitised logs are often the only remaining copy of an incident. |
| `automation.bulk-find-and-replace-planner` | can rewrite every matching file in a directory tree when `write=true`. |
| `automation.batch-rename-text-generator` | produces renames that could overwrite existing files. |
| `automation.golden-sample-and-regression-builder` | records a baseline that later regression tests will trust. |
| `meta.end-to-end-text-production-director` | runs a multi-stage pipeline over the input and labels the result. |

Gating is enforced inside the handler through one shared gate, so it applies however the capability is invoked - CLI, pipeline, or another capability. The preview is produced **before** the gate, so `--dry-run` or a bare run always shows what would change.

### 7.2 Data handling

- No network calls, no telemetry, no uploads: input text never leaves the machine.
- No credentials, API keys or service endpoints are embedded anywhere in the folder. Nothing needs a key to run.
- Detections are local regex/table heuristics. They reduce risk; they do not certify compliance.
- Placeholder maps produced by redaction are returned in memory only; the tool never writes a map back to disk next to the redacted text.
- Log and secret scanners report *offsets and types*, not surrounding context, so a report can be shared more safely than the raw log.

### 7.3 Honesty rules built into the output

- A capability that guesses says so in a `warning`: auto-fitted verse, estimated caption timings, approximate IPA, reconstructed FEN, lexicon sentiment.
- `validate` never returns a score without listing the failed checks.
- Provenance labels record what you declare; the data field `claims_verified` stays `false`.
- Everything time-, version- or standard-sensitive (Unicode versions, romanisation systems, citation styles, accessibility limits) is presented as guidance with a pointer to the authoritative source, not as permanently correct data.

---

## 8. Validation and tests actually performed

Everything below was executed on this build, in this order, before packaging.

| # | What was checked | How | Result |
| --- | --- | --- | --- |
| 1 | Help and capability index | `help`, `list --categories`, `list --category typography` | Lists all 20 categories and 240 capabilities with engines and parameters. |
| 2 | Parameter contract | `info <cap>` for every module, then runs with defaults | Every capability exposes its parameters and defaults; unknown names are rejected with hints. |
| 3 | Every local capability executes | Automated sweep: all 211 local capabilities run with `--json` on a mixed sample | 172 returned `status: ok`; 38 returned a structured `INVALID_INPUT` because the sample lacked the specific shape that capability needs; 1 returned a structured `NOT_FOUND` for a missing directory. No tracebacks, no unparseable envelopes. |
| 4 | Bundled fixtures | `selftest` (39 capability fixtures + 17 structural checks) | 56/56 after the readme was added; see the run log in section 8.1. |
| 5 | Invalid input | Empty text, too-short text, unparseable dates, missing second text | `INVALID_INPUT` with advice, exit code 2. |
| 6 | Invalid parameters | Unknown name, bad enum value, out-of-range integer | `INVALID_PARAM` with the accepted list, exit code 2. |
| 7 | Missing input | `run ... --text-file does-not-exist.txt` | `NOT_FOUND`, exit code 5. |
| 8 | Approval enforcement | Gated capabilities without `--approve`, then with | `APPROVAL_REQUIRED`, exit code 3, then success. |
| 9 | Changed input changes output | Same capability, two inputs, output compared | Output and `data` differ as expected (see section 8.2). |
| 10 | Recovery path | Corrupted file through the repair capability, then re-run of the cleaned file | Output readable, helper identities preserved (see section 8.3). |
| 11 | Relative paths beside a SKILL.md | Folder copied into a clean temp root next to a `SKILL.md`, run with relative paths | Passed (see section 8.4). |
| 12 | Distributable archive | Folder zipped at the archive root, extracted into a clean temp dir, `selftest` re-run | Passed (see section 8.5). |
| 13 | Packaging rules | `selftest` structural checks: prefixes, no subfolders, one knowledge file, no `SKILL.md`, no archives, no absolute paths in this readme | Passed. |

### 8.1 Bundled fixture run

```bash
python3 tools-ultimate-text.py selftest
```

```
56/56 checks passed (39 fixture(s), 17 structural check(s))
```

The fixtures cover creation and editing, Unicode and invisible characters, text art and emoji, typography and languages, structured formats, professional and analysis work, safety redaction, secret scanning, injection marking, homoglyph detection, checksums, corruption repair, captions, transcripts, maths notation, braille, determinism, format interoperability, the readiness gate, pipelines, routing and catalog maintenance - plus negative cases that assert the *errors*.

### 8.2 Changed input changes output

```bash
python3 tools-ultimate-text.py run analysis.key-information-extractor --text 'Ana wrote on 2024-05-06.'
python3 tools-ultimate-text.py run analysis.key-information-extractor --text 'Ben wrote on 2024-09-01 about 12 EUR.'
```

The first returns only a `dates` row; the second returns `dates` and `money` rows with different values, and the `data.counts` differ. The same check is asserted for the sentiment, numeral, captions and pipeline fixtures in `selftest`.

### 8.3 Recovery path

```bash
printf 'Caf\xc3\x83\xc2\xa9 meeting\r\n' > broken.txt     # mojibake + CRLF
python3 tools-ultimate-text.py run integrity.text-corruption-repairer --text-file broken.txt --approve
python3 tools-ultimate-text.py run typography.typography-cleaner --text-file repaired.txt
```

The repairer reports `mojibake-repair`, `newline-normalisation` and `unicode-NFC` steps and returns `Café meeting`. The repaired file is then processed normally, which is the documented recovery sequence: repair first, edit second, and verify with `integrity.encoding-forensics-analyst`.

### 8.4 Relative paths beside a test SKILL.md

The folder was copied into a scratch root beside a dummy `SKILL.md` and every command was run with relative paths only (`--text-file input.txt`, `--out result.txt`, `--cwd .`). Nothing resolved outside the folder and no command needed an absolute path.

### 8.5 Archive round-trip

The distributable archive is `ultimate-text-skill-tool.zip`. It keeps the `ultimate-text/` Skill Tool folder directly at the archive root: no wrapper folder, no nested archives, 26 files, and no `__pycache__` or editor leftovers. After extraction into a clean temporary directory, this ran successfully:

```bash
cd ultimate-text
python3 tools-ultimate-text.py selftest            # 56/56 checks passed
python3 tools-ultimate-text.py list --categories   # all 20 categories
python3 tools-ultimate-text.py run text-art.figlet-style-banner-maker --text "OK" --set font=block
python3 tools-ultimate-text.py run automation.text-pipeline-architect --text-file draft.txt \
    --set "pipeline=typography.typography-cleaner|creation.text-quality-finisher"
python3 tools-ultimate-text.py run meta.text-skill-catalog-maintainer   # 240 capabilities, 211 handlers, no problems
```

---

## 9. Limits and unavailable integrations

These are deliberate, documented boundaries. Anything here is *not* implemented, so no result should be read as covering it.

| Area | Limit |
| --- | --- |
| Language coverage | Lexicons, sentiment, readability, filler/jargon maps and IPA rules are English-first. Other languages get script handling, casing, transliteration tables and plural categories, not full linguistic analysis. |
| Generation | No capability invents long-form prose on its own. The 29 agent-assisted capabilities produce a contract for the calling assistant; the local ones transform, extract, format or generate from templates and seeds. |
| Network | No fetching, no link checking, no live standard lookups, no translation APIs. `retrieval.*` returns canonical references you must open yourself. |
| Standards data | Unicode tables, romanisation systems, citation styles, accessibility thresholds and currency lists are snapshots. They are version-sensitive; confirm against the authoritative source before publishing. |
| Unicode coverage | Confusables, transliteration and symbol tables are curated subsets, not the full Unicode Character Database. Rare scripts and newer emoji may be missing or flagged unknown. |
| Format parsing | The YAML reader supports a practical subset (mappings, lists, scalars, block text). Anchors, aliases, complex keys and multi-document streams are rejected rather than guessed. CSV handling is RFC 4180-style but not a full parser for every dialect. |
| NLP accuracy | Sentiment, topics, argument mapping and classification coverage are heuristic and reported with their method. They are useful for triage, not for final judgement. |
| Vision and audio | No image, audio or video decoding. Captions, transcripts and OCR input must arrive as text (or as text with timestamps). |
| Braille | Grade-1 English only; no contractions, no eight-dot, no other languages. |
| Legal, medical, financial advice | Capabilities flag and restructure; they never advise. Disclaimer insertion is offered, and the review of high-stakes text stays with a qualified human. |
| Filesystem work | The automation capabilities plan and can apply replacements in a directory you name, bounded by `max_files`; they do not traverse outside that directory and they never follow symlinks out of it. |
| Performance | Pure-Python single pass. Very large inputs are capped by `max_input_bytes` (4 MiB by default) and per-capability timeouts rather than streamed. |
| Concurrency | Single process, no shared cache, no lock files. Safe to run several instances in parallel because nothing is shared. |

---

## 10. Troubleshooting, recovery, extension, maintenance

### 10.1 Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `FATAL: cannot load the Skill Tool modules` (exit 8) | a runtime file is missing, renamed, or two modules export the same helper | Keep every `tools-*.py` from the package in one folder; the message names the offending file and symbol. |
| `INVALID_PARAM ... did you mean` | typo in `--set` | Use the suggested name, or `info <cap>` for the full list. |
| `INVALID_INPUT input text is too short` | capability needs more context than supplied | Give the minimum length stated in the message, or choose a capability that needs less. |
| Mojibake in the output | wrong input encoding | Re-run with `--encoding cp1252` (or the right codec), or repair first with `integrity.text-corruption-repairer`. |
| `APPROVAL_REQUIRED` | gated capability | Review the preview, then re-run with `--approve`. |
| `TIMEOUT` | large input or a pathological regex | Raise `--timeout`, simplify the pattern, or split the file. |
| Output looks right but a warning says it is estimated | heuristic result (timings, verse, IPA, FEN) | Treat it as a draft and confirm against the source. |
| Different output on another machine | locale/encoding differences in the shell | Pass `--encoding utf-8` and `--config` with an explicit `locale`; seeded capabilities are deterministic for a fixed `--set seed`. |

### 10.2 Recovery sequence for damaged text

1. `integrity.encoding-forensics-analyst` - identify what is wrong (mojibake, control characters, line endings).
2. `integrity.text-corruption-repairer` - repair with `--approve`, keeping the original file.
3. `integrity.steganographic-text-pattern-checker` - confirm no hidden characters remain.
4. `typography.typography-cleaner` - normalise the rest of the typography.
5. `integrity.checksum-and-text-fingerprint-helper` - record the checksum of the recovered text.

### 10.3 Extension: adding a capability

1. Add the capability to `asset-capability-catalog.json` in the right category with `id`, `name`, `description`, `engine: "local"`, `params` (typed), `requires_approval`, `timeout_s` and `acceptance_checks`.
2. Implement a handler in the matching `tools-*.py` module and register it in that module's `HANDLERS` dict under the same id. Return `{"output": str, "data": dict, "warnings": [...]}`.
3. Raise `core.InvalidInput` / `core.InvalidParam` for user errors; call `ctx.gate("what will happen")` before anything destructive.
4. Add a fixture to `tools-selftest.py` and run `selftest --filter <category>`.
5. Run `meta.text-skill-catalog-maintainer`; it fails if a local capability has no handler, or a default is outside its enum or range.

Helpers worth reusing instead of rewriting: `core.sentence_split`, `core.word_tokens`, `core.table`, `core.truncate`, `core.seeded_rng`, `core.write_text_atomic`, and from `tools-text-ops.py`: `count_syllables`, `reading_metrics`, `summarize_extractive`, `word_freq`, `replace_preserving_case`, `title_case`, `sentence_case`.

### 10.4 Maintenance

- `selftest` is the regression gate: run it after any edit to a module, an asset or the catalog.
- `meta.text-skill-catalog-maintainer` validates catalog/handler coherence and parameter defaults.
- `automation.golden-sample-and-regression-builder` records baselines; compare with `--set action=compare --set expected_file=...` after intentional changes.
- `automation.text-conversion-qa-auditor` re-checks the format converters after changing them.
- When refreshing standards data, update the relevant `asset-*.json` and bump its `version` field; the readme's limits section already tells readers the data is a snapshot.
- Asset files are validated on every `selftest` run (parsing plus structural checks), so a malformed JSON edit fails loudly instead of at first use.

---

## 11. Engineering notes

- **Determinism.** Capabilities that generate content take `--set seed=...` and use one seeded RNG helper, so the same inputs produce the same output. `automation.deterministic-output-controller` re-runs a normalisation and reports whether the result is stable.
- **One shared runtime.** Limits, error types, atomic writes, timeouts, the approval gate and text helpers all come from `tools-core.py`; capability modules only implement behaviour. That is why a new capability inherits validation, structured errors, limits and safety for free.
- **Flat module loading.** Module filenames use hyphens for the packaging rules, so `tools-loader.py` loads them by path with fixed aliases, injects `core` and a merged helper namespace, then binds every handler. Load order is explicit and the loader fails loudly on a missing file, a duplicate helper or a duplicate handler.
- **No hidden state.** Nothing is cached between runs, no file outside the paths you pass is modified, and `--dry-run` never writes.
- **Structured by default under `--json`.** `data` is the machine-readable part: counts, tables, offsets, confidences and method notes; `output` is the text a human reads.

---

## 12. Limits reference

| Limit | Value |
| --- | --- |
| `max_input_bytes` | 4 MiB input |
| `max_output_bytes` | 8 MiB output |
| `max_input_lines` | 200000 lines |
| `max_regex_pattern` | 2000 characters per pattern |
| `max_batch_files` | 500 files per automation run |
| `default_timeout_s` | 20 s default per capability |
| `max_timeout_s` | 300 s ceiling for `--timeout` |
| `max_variants` | 50 generated variants per call |

These are enforced in `tools-core.py` and surface as `LIMIT_EXCEEDED` (exit 7) with the limit that was hit, so a caller can split the work instead of guessing.

---

_Ultimate Text Skill Tool - 240 capabilities, 26 files, standard library only, no network._

