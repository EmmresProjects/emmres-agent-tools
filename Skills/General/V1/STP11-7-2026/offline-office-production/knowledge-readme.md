# Offline Office Production

## Purpose and scope

A reusable offline command-line **component**, not a complete Copilot skill. Place this folder beside the future parent's one root SKILL.md. No SKILL.md is included here. Python is practical because native OOXML, PDF, raster and cryptography libraries are available and were exercised locally; a browser app would not provide this filesystem workflow reliably offline.

**Important acceptance boundary:** the source PDF is a planning catalog of 480 proposed skills in 32 categories, not an implementable specification of Office, OCR, signing and standards engines. This component delivers the concrete operations below and preserves every catalog name and description. It does **not** implement all 480 named skills in full or meet the PDF's final acceptance standard. `assess` reports every catalog entry as partial foundation only and release-blocked. The catalog is not presented as 480 working handlers. This is usable tested code, but it is not a certified, complete office production suite or a malware sandbox. Do not release regulated or confidential material solely on its results.

No provider, model, cloud endpoint, paid credential or network operation is built in. Native document content remains editable where authored as OOXML. Extraction/diff are intentionally lossy views, not native conversion.

## File architecture and loading

All **11 files** are directly in this folder:

| File (relative to the future skill root) | Functional role |
|---|---|
| `offline-office-production/tools-office.py` | Primary CLI, dispatch, JSON error contract, catalog readiness, batch recovery, release gate |
| `offline-office-production/tools-common.py` | Shared bounds, safe paths, atomic artifact writes, approval and archive validation |
| `offline-office-production/tools-native.py` | DOCX/PPTX/XLSX/PDF authors, text extractors, OOXML/PDF structural inspection |
| `offline-office-production/tools-pdf.py` | Page operations, raster rendering, forms, encryption, redaction |
| `offline-office-production/tools-workflows.py` | Catalog normalization, mail merge, diffs, environment baseline, records, local engine adapters |
| `offline-office-production/tools-selftest.py` | Reproducible CLI integration tests in an isolated future skill root |
| `offline-office-production/tools-requirements.txt` | Exact tested direct Python dependency versions, consumed by pip |
| `offline-office-production/asset-capability-catalog.json` | All 480 extracted catalog entries, used by catalog/assess and category normalization |
| `offline-office-production/asset-release-policy.json` | Editable fail-closed list of release evidence requirements, consumed by gate |
| `offline-office-production/asset-example-request.json` | Runnable authoring request and editable starting template |
| `offline-office-production/knowledge-readme.md` | This single consolidated guide |

The primary entry loads common, then native/PDF/workflow modules from its own folder with fixed names. Only the primary CLI and self-test are standalone commands. Supporting modules are separated by runtime responsibility, not tool-count targets. Their names contain hyphens intentionally; do not rename or import them using conventional Python module syntax. No untrusted module names are accepted.

## Setup and exact commands

Tested environment: Linux, CPython 3.13. Other operating systems and Python versions have **not** been validated. POSIX shell (`sh`) commands below run from the future skill root (the directory containing this folder). A compatible Python interpreter must already be installed. All CLI input/output paths are relative to this current directory and must stay within it.

Exact tested direct dependencies are in `offline-office-production/tools-requirements.txt`: python-docx 1.1.2, openpyxl 3.1.5, python-pptx 1.0.2, pypdf 7.0.0, reportlab 5.1.0, PyMuPDF 2.1, cryptography 50.0.2, Pillow 13.0.0. PyMuPDF reports MuPDF 1.28.6 in the tested environment. Versions are pinned to the observed installed distributions, not guessed public release versions. These libraries materially supply native Office serialization, PDF objects/forms/crypto, PDF rendering/redaction, and raster verification.

This ZIP contains code, not a vendored Python distribution, wheels, fonts, OCR models or operating system applications. Dependencies must be staged by an authorized operator; offline installation itself has not been tested in a pristine OS. Direct dependency versions are pinned; transitive dependencies and OS libraries are **not** a complete locked air-gap deployment bundle. Resolve, review licenses and lock transitive wheels for the target platform before claiming air-gap install readiness. PyMuPDF/MuPDF uses AGPL/commercial dual licensing; assess obligations for your deployment. No third-party source or binary is redistributed here.

If the operator supplies compatible approved wheels in a directory named `dependency-wheels` beside the component, an explicit offline installation command is:

```sh
python -m pip install --no-index --find-links dependency-wheels -r offline-office-production/tools-requirements.txt
```

`dependency-wheels` is **operator-supplied input, not an included resource**. Do not run a privileged install automatically. In an already provisioned Python environment:

```sh
python offline-office-production/tools-office.py --help
python offline-office-production/tools-office.py capabilities
python offline-office-production/tools-office.py run baseline
python offline-office-production/tools-office.py run create --input offline-office-production/asset-example-request.json --output creation-result.json
python offline-office-production/tools-selftest.py
```

The sample creates `report.docx` and a JSON report. Reusing the same destination is refused unless the operator reviews and approves replacement with `--approve`. For a new run, change `dest` in a copy of the sample rather than overwriting unreviewed work.

## CLI contract, paths and common configuration

```sh
python offline-office-production/tools-office.py run OPERATION --input request.json --output result.json --timeout 120
```

`request.json`, `result.json` and all artifact destinations in examples are **operator-created runtime inputs/outputs**, not missing packaged resources. `--output` is optional. `--input` is optional only for baseline and assess. JSON UTF-8 requests must be objects. Success produces JSON on stdout and optionally the same JSON in the configured report file:

```json
{"ok": true, "operation": "extract", "result": {"text": "Example", "characters": 7}}
```

Errors produce `{"ok":false,"error":{"code":"missing_input","message":"..."}}` and exit 2. A failed batch exits 1, retains successful results and includes its recovery cursor. Cancellation exits 130. Help exits 0. Findings, a blocked gate or partial readiness are valid analysis results and return exit 0; **inspect the result**, not only the exit code. `gate.result.released` must be true before considering an operator-approved release. No actual publishing/release action is performed.

Only relative forward-slash paths are accepted. Absolute paths, backslashes, parent traversal and symlink escape outside the current working root are rejected. Storage is configurable through `dest`, `dest_prefix`, `--output` and choice of working root; no fixed storage location is used. Resource loading is local to the component. Filenames and all references are case-sensitive. Work in a private writable root because outputs may contain confidential text or decrypted content.

### Complete implemented operation list and request schemas

Unknown operation names fail. Fields not described here are not feature promises; currently extra request fields are ignored. Optional fields have the defaults stated below. Arrays are bounded; malformed nested values may report `operation_failed` rather than a field-specific validation code, but are not committed as completed artifacts.

#### 1. create — native authoring

Common: `format` (docx, pptx, xlsx, pdf), `dest` (matching extension), optional `title` (default Untitled).

DOCX/PDF use `blocks`: array of objects. Supported block types are `paragraph`, `heading`, `bullet`, `table`. Text blocks require `text`. DOCX heading `level` defaults 1 and is clamped 1–9; PDF uses Heading2 for all heading blocks. DOCX bullets use List Bullet; PDF bullet blocks currently use plain body styling. Table blocks require rectangular `rows` arrays with 1–100 columns. DOCX gets a native Table Grid. PDF tables are basic ReportLab tables; long-content layout may fail rather than silently clip. Tables are not accessibility-tagged.

```json
{"format":"docx","dest":"memo.docx","title":"Memo","blocks":[{"type":"heading","text":"Decision"},{"type":"paragraph","text":"Approved for review."},{"type":"table","rows":[["Item","Value"],["Count",3]]}]}
```

PPTX requires nonempty `slides`, each with `title`, optional string-array `bullets`, optional string `notes`. Creates editable title/content slides with real speaker notes; no auto-fit, master design engine, animation, media or slide rendering is claimed.

```json
{"format":"pptx","dest":"briefing.pptx","title":"Briefing","slides":[{"title":"Status","bullets":["Offline build","Review needed"],"notes":"Discuss risks."}]}
```

XLSX requires 1–100 `sheets`, each with unique valid `name` and scalar rectangular-or-ragged `rows` (up to 1000 rows and 100 columns per row). First row is bold, pane frozen at A2, native autofilter set. Optional `chart:true` creates one native column/bar chart from column 1 categories and remaining numeric series; at least a header and one data row/two columns required. `chart_title` defaults to sheet name. Optional `validations` is an array of `{range, values}` with a simple string-array inline list (maximum 255 encoded characters; no comma, quote or newline inside a choice).

```json
{"format":"xlsx","dest":"metrics.xlsx","sheets":[{"name":"Metrics","rows":[["Month","Units"],["Jan",3],["Feb",5]],"chart":true,"validations":[{"range":"A2:A3","values":["Jan","Feb"]}]}]}
```

Formula strings beginning `=` are written only if `allow_formulas:true` **and** `--approve` are supplied. Basic external/active formula patterns are refused. This filter is not a complete formula security parser. No formulas are evaluated, no cached results are calculated, and no pivot table or data model engine exists. Use an approved offline spreadsheet application to recalculate and verify. Keep untrusted formulas out of production; authoring editable formulas does not prove their correctness.

PDF authoring creates untagged final pages with built-in Latin fonts. Unsupported non-Latin text is rejected. Native OOXML strings accept Unicode but shaping/fonts, RTL layout and localization have not been verified. Fonts are not embedded/pinned by this tool. Test all content visually before release.

#### 2. extract — body text

`source` required, optional `dest` for UTF-8 text. Supports docx paragraphs and top-level tables; pptx text-bearing shapes; xlsx sheet names, cells and formula strings; PDF searchable text; UTF-8 txt/md/csv/html read literally. Markdown/HTML are not rendered. Headers, notes, nested layouts, images, comments, tracked-change semantics and rich structure are not fully extracted. Encrypted PDF requires a separate approved decrypt operation first. Workbook limit is 100,000 cells per sheet. Scanned PDFs may legitimately return empty text; use the local OCR adapter after raster preparation if available.

```json
{"source":"memo.docx","dest":"memo.txt"}
```

#### 3. inspect — structural and heuristic checks

`source` required (docx/pptx/xlsx/pdf). OOXML: archive CRC/bounds, required primary parts, XML parsing, macros/ActiveX/embedded-object part detection, external relationships, revision/comment presence, selected missing alt-text attributes and formula count. PDF: encryption state, page count, metadata, form count, basic document/page/annotation action flags and missing structure tree. `statistics`, `findings` and `release` are returned. Any finding blocks heuristic release; no findings still means manual review required.

```json
{"source":"memo.docx"}
```

This is **not** a comprehensive active-content scanner, malware detector, accessibility validator, signature verifier or PDF preflight. Metadata and relationship targets in reports may themselves contain sensitive information.

#### 4. pdf — page, form, raster and privacy operations

All actions require `action` and `dest`. Existing digital signatures may be invalidated by **any** PDF modification; signed-document workflows are unsupported. Inputs should be reviewed before processing.

| Action | Additional fields | Responsibility |
|---|---|---|
| merge | `sources`: nonempty path array | Append PDFs in order; at most 1000 total pages |
| select | `source`, `pages`: nonempty 1-based integer array | Extract/split, duplicate or reorder selected pages into one output; use batch for multiple outputs |
| rotate | `source`, `degrees`: 90/180/270 (default 90) | Rotate every page |
| render | `source`, optional `page` (1), `dpi` (96) | Rasterize one page to a `.png`; DPI 36–150, at most 25 megapixels |
| form-create | `fields`: array, optional `title` | Create one-page A4 text-field AcroForm; unique field `name`, optional `label`, `value`, `x` (100), `y` (700), `width` (240), `height` (20), coordinates in bottom-left PDF points |
| form-fill | `source`, `values`: object of existing field names to strings | Fill supported existing text fields; no flattening or signature fields |
| encrypt | `source`, `new_password`: at least 12 characters | AES-256 password encryption; requires approval |
| decrypt | `source`, `password` | Decrypt to a separate or explicitly approved target; requires approval |
| metadata-clear | `source` | Remove ordinary PDF document metadata; requires approval; not complete sanitization |
| redact | `source`, nonempty string-array `terms` | Apply actual redaction to searchable matches, rebuild/compact output and reject commit if terms remain in extracted text; requires approval |

Optional `password` opens encrypted sources for supported PDF actions. Passwords are read from the request, not echoed deliberately, but the input JSON is sensitive: protect it with OS permissions and remove it according to organizational policy. The tool never securely erases files or manages credentials.

```json
{"action":"select","source":"input.pdf","pages":[3,1],"dest":"excerpt.pdf"}
```

```json
{"action":"form-create","dest":"survey.pdf","title":"Survey","fields":[{"name":"name","label":"Name","x":100,"y":700}]}
```

```json
{"action":"redact","source":"input.pdf","terms":["confidential"],"dest":"redacted.pdf"}
```

Redaction is **text-match removal only**, not a guarantee of sensitive-content removal across scans, attachments, hidden text, metadata, object streams or screenshots. No matches produces `no_matches`; no misleading black-box-only output is committed. Review the raster result and inspect with independent tools. No forensic privacy claim is made.

#### 5. compare — deterministic text differences

`left`, `right` required; optional `dest` for unified UTF-8 diff. Supports all extractable formats. Returns `equal`, `diff`, scope warning. At most 20,000 total text lines. This is not pixel, layout, tracked-change or semantic Office comparison.

```json
{"left":"before.docx","right":"after.docx","dest":"review.diff"}
```

#### 6. mail-merge — bulk editable DOCX

`template` DOCX path, nonempty array `records`, and `dest_prefix` required. Tokens are `{{field}}`, where field uses ASCII letters/digits/dots/hyphens. Every record must contain every used token. Outputs are prefix + `-0001.docx`, `-0002.docx`, etc. Supports body paragraphs, top-level table cell paragraphs and section header/footer paragraphs. Split-run tokens are recognized by paragraph text, but the replaced paragraph is consolidated to its first run's formatting. Nested tables/text boxes/fields are not merged. Preflights missing tokens and existing destinations before the first commit. Output conflicts need approval. A template with no supported tokens fails.

```json
{"template":"letter.docx","records":[{"name":"Ada"},{"name":"Lin"}],"dest_prefix":"letters"}
```

Create `letter.docx` containing `Dear {{name}}` first. Mail merge does not send or publish anything.

#### 7. parse-catalog — reusable input normalization

`source` required, optional `category` umbrella and `dest` JSON output. Reads UTF-8 plain text, Markdown or copied HTML, plus PDF and DOCX. Ignores HTML attributes/classes, scripts/styles, common list marks, Markdown headings and excess whitespace. Recognizes `Skill Tool Name:`, `Skill Name:`, `Name:`, `Description:` and `Category:`. Unlabeled names followed by Description are supported; a two-or-more-line unlabelled single capability uses first line as name and remaining lines as description. Existing catalog category-only input resolves that category's 15 entries. An unknown category-only line fails rather than inventing capabilities. For a new multi-category catalog, use explicit `Category:` lines. Complex malformed HTML, arbitrary unlabeled tables and ambiguous inline prose are not guaranteed to normalize correctly; inspect output before use.

```json
{"source":"capabilities.html","category":"Document production","dest":"normalized.json"}
```

Returns `{capabilities:[{id,category,name,description}],count}`. Parsed IDs are stable sequential `parsed-001` within that input; built-in category lookup retains the source IDs. Parsing never generates or executes code. The 61-page attached source PDF was fully parsed (480 entries) using this interface; unlike a page-limited web fetch, local PDF extraction reads all pages.

#### 8. assess and catalog — full source coverage without false support claims

```sh
python offline-office-production/tools-office.py catalog
python offline-office-production/tools-office.py catalog --id c01-01
python offline-office-production/tools-office.py catalog --category Accessibility
python offline-office-production/tools-office.py run assess --input assessment.json
```

Assessment request: optional `ids` array, default all 480. Unknown IDs fail. Each entry contains exact source category/name/description, `status:partial-foundation-only`, `release:blocked` and limitation text. Includes local environment and required external validations. It deliberately does not map a broad catalog skill name to a narrowly matching operation and claim the whole skill works.

All categories retained: environment readiness; DOCX authoring; review/revisions; PDF pages; forms/signatures; PDF standards; PPTX authoring; presentation delivery; XLSX formulas; tables/pivots/models; spreadsheet charts; data validation/import; automation sandboxing; Microsoft Office compatibility; LibreOffice compatibility; conversion/round trips; OCR/handwriting/layout; accessibility; fonts; templates; mail merge; citations; comparisons; privacy; signing/trust; printing; structured forms; localization/RTL; records/compliance; QA/regression; backup/handoff; documentation/governance. Exact names and all descriptions remain in the runtime catalog, not replaced by this summary.

#### 9. baseline — environment readiness facts

No request required, or `{}`. Reports Python/OS, installed direct package versions and boolean availability of local LibreOffice, Tesseract, veraPDF, qpdf, printer CLI, OpenSSL and font listing commands. No application installation, probing device access, downloading or executing those engines occurs. Presence is not proof of readiness. No absolute machine paths are emitted.

#### 10. adapter — optional local office conversion/OCR

Required: `adapter` (`libreoffice-pdf` or `tesseract-text`), `source`, `dest`. Optional `timeout` integer 1–120 seconds (60), OCR `language` (eng; alphanumeric language identifiers joined by `+`). Requires `--approve` to launch an installed engine. Engine lookup uses the operator-controlled PATH; use a trusted PATH, not a writable document directory. There is no arbitrary executable, command, plugin, shell or argument-template input.

LibreOffice accepts docx/pptx/xlsx/odt/ods/odp to `.pdf` with a fresh temporary profile. Tesseract accepts png/jpg/jpeg/tif/tiff up to 25 megapixels and returns UTF-8 text; language models must already be provisioned. No table recovery, handwriting recognition, OCR confidence scoring, layout reconstruction or native OOXML round-trip preservation is implemented.

```json
{"adapter":"libreoffice-pdf","source":"memo.docx","dest":"memo.pdf","timeout":60}
```

```json
{"adapter":"tesseract-text","source":"scan.png","dest":"scan.txt","language":"eng"}
```

**Unavailable here:** both local applications. Their fail-closed missing-engine responses were tested; successful conversion/OCR was not tested. The adapter has temporary profiles and timeouts but **is not an OS sandbox**. Process untrusted files in an independently isolated, nonprivileged, network-denied OS environment. Subprocess timeout kills the directly launched child; recursive descendant-process containment is not provided. No sandboxing, visual quality or active-content safety guarantee follows from approval.

#### 11. records — backup/recovery/provenance

| Action | Request | Result |
|---|---|---|
| backup | `sources` path array, `dest` | CRC-bearing ZIP containing input relative paths; no nested archive inputs |
| verify | `source` ZIP | Archive bounds/path/CRC validation and member names |
| restore | `source` ZIP, `dest_prefix` | Safe bounded extraction using validated paths and atomic individual writes; requires approval |
| digest | `source` | SHA-256 and size for provenance comparison |

```json
{"action":"backup","sources":["memo.docx","briefing.pptx"],"dest":"handoff.zip"}
```

Runtime backup ZIP output is a user-requested work product, not an archive nested in the delivered component ZIP. CRC is not authenticity; SHA-256 alone is not a signature, evidence chain or immutable retention policy. Restore is per-file atomic, not transactional across a set; interrupted restores may be rerun after review. No deletion, legal hold enforcement, immutable archive or secure erasure exists.

#### 12. gate — fail-closed quality release decision

Requires `source` and `evidence` object. Loads required boolean checks from `offline-office-production/asset-release-policy.json`, requires each to be JSON `true`, and blocks on structural findings. Empty evidence fails. No network, publishing or filesystem promotion occurs.

```json
{"source":"memo.docx","evidence":{"visual_review_passed":true,"native_reopen_passed":true}}
```

This deliberately remains blocked until all configured requirements are provided. Evidence covers visual review, reopening, compatibility, accessibility, security/privacy, formulas, signatures, archival and clean-state tests. For truly inapplicable checks, obtain operator review and adjust the policy explicitly; do not falsely attest a test. Evidence is an **operator assertion**, not an independently verified report. Do not treat gate as PDF/A, PDF/UA, signature or accessibility certification.

#### 13. batch — manifest execution and recovery

`jobs`: array of at most 100 `{operation,request}` objects; optional `resume_from` zero-based cursor (0). No nested batches. Executes only built-in operation names in order and stops at first failure. Returns completed results and the failing cursor. No rollback is promised for committed jobs.

```json
{"jobs":[{"operation":"create","request":{"format":"docx","dest":"batch.docx","title":"Batch"}},{"operation":"extract","request":{"source":"batch.docx","dest":"batch.txt"}}]}
```

Recovery: fix the failing request, set `resume_from` to the returned index and rerun the manifest. Do not set approval merely to suppress conflicts; determine which outputs completed first. An adapter is never automatically retried. There are no network retries. Explicit batch resume is the only retry mechanism.

## Safety, limits and recovery

- Approval is explicit `--approve`, scoped by operator review of the full request. It is required for overwrite, formula authoring, local engine launch, encryption/decryption, redaction, metadata removal and restore. A batch approval applies to all its jobs; review the whole manifest. This is an approval **gate**, not identity authentication or a tamper-proof audit system.
- Normal new-file authoring does not require approval. No installation, privileged operation, payment, publication, credential provisioning, macro execution or arbitrary code/shell execution is exposed.
- Inputs and individual outputs are limited to 64 MiB. Arrays generally to 1000 elements, sheets to 100, batch jobs to 100, PDF sources to 1000 pages; merged PDF to 1000 pages. Archive input: at most 5000 members, 128 MiB declared expanded total, 64 MiB each member, compression ratio at most 500, no encrypted members or traversal. Restore limit is 1000 files. These bounds may reject legitimate highly compressible documents; lower-size exports are the recovery path.
- Writes use a temporary sibling file then atomic replace. Exceptions/cancellation clean the uncommitted temporary artifact. Committed outputs are never automatically removed. Fatal OS kill may leave `.office-` temporary files; review and remove only known abandoned files manually. The tool contains no automatic cleanup/deletion command.
- Overall time budget is 1–300 seconds, default 120. POSIX alarm and cooperative checks enforce it on the tested Linux runtime. C extensions or subprocess internals can delay signal delivery; use an external OS supervisor for hard CPU/memory/process limits. Windows timing behavior is untested. Ctrl-C requests cancellation; do not assume cancellation undoes committed output.
- Zip/XML structural heuristics are not an exploit-resistant sandbox. Avoid opening untrusted documents on privileged hosts. Native parsers are third-party dependencies and must be maintained.
- Changes to signed PDFs can invalidate signatures. No PDF/CMS signing, trusted timestamping, certificate revocation, chain validation, secure key storage or signer identity verification is implemented. Never pass keys to an unsupported interface.
- Reports, extracted text, decrypted files and requests containing passwords are sensitive. Permissions, retention, encrypt-at-rest, log handling and backups remain operator responsibilities. No secrets are included in the component.
- Inspect `error.code` first: `missing_input`, `invalid_json`, `invalid_input`, `path`, `limit`, `approval_required`, `password`, `unsafe_archive`, `dependency_missing`, `integration_unavailable`, `timeout`, `unsupported`, `no_matches`, `redaction_failed`, `operation_failed`. Error details from third-party parsers may vary; they are not always field-specific.

## Validation actually completed

The bundled self-test passed **19 tests**, exercising the real subprocess CLI from a clean temporary root with one test parent SKILL.md beside the component. It covers:

1. Help, capability listing, all 480 catalog entries/32 categories, valid and missing catalog IDs.
2. Author/reopen/extract/inspect DOCX, PPTX, XLSX and PDF; native workbook chart/data validation and speaker-note authoring.
3. Changed input producing changed text diff and identical-input comparison.
4. PDF merge, selection/reorder/duplication, rotation, PNG rendering and raster decode; invalid page rejection.
5. Text AcroForm creation/fill and field-value reopening; unknown-field failure.
6. Approval denial; AES-256 encryption, incorrect password rejection, successful decrypt, metadata clear, applied redaction and removed-text check; no-match failure.
7. Two-record native DOCX merge, actual replacement text and missing-token failure.
8. HTML/CSS ignoring and Markdown catalog normalization.
9. Built-in category-only input, unlabeled numbered list and single capability normalization.
10. Environment baseline, two-entry blocked assessment and invalid ID.
11. Backup CRC validation, approval-denied restore, approved recovery, matching SHA-256.
12. Missing-evidence release block and complete-operator-evidence gate path.
13. Batch failure, persisted structured report, recovery cursor and resume without rerunning the successful job.
14. Missing/invalid JSON, bad operation/extension, absolute/traversal path denial, overwrite denial and explicit approved replacement.
15. Formula authoring approval, formula-string reopening, external-function rejection. **No calculation test was performed.**
16. Both unavailable local engine adapter error paths. **Successful OCR/LibreOffice integration was not performed.**
17. Malicious traversal ZIP verification and restore rejection.
18. Embedded macro-part detection without execution.
19. Flat prefixed file structure, exactly one knowledge file, no component SKILL.md and packaged sample execution.

Separately, the supplied PDF's complete 61-page catalog was parsed through `parse-catalog` and checked for 480 results. The final delivery ZIP was opened, CRC-tested, inspected for a single root component/no wrappers/no archives/no internal subfolders, extracted into a clean temporary skill root beside one test SKILL.md, and the primary help/capability commands and the complete self-test suite rerun from the extracted files. All passed. Every packaged asset parses as JSON; every Python tool parses/executes through the suite; every pinned dependency matches the tested environment. Referenced packaged resources were checked with exact spelling.

PDF raster output was decoded as a PNG and generated native outputs were reopened with their format libraries. **No human visual review, Microsoft Office/LibreOffice UI test, independent renderer comparison, printer trial, assistive technology test, PDF/A/UA/X certification, digital signature verification, cold offline installation or deployment rollback test was performed.** File creation and library reopening alone are not final production acceptance.

## Catalog gaps and integration boundaries

Native authoring/inspection do not implement full tracked changes, comments editing, revision acceptance, pivot engines, Power Query/models, native formula evaluation, media-rich presentations, Office deployment, font installers, driver provisioning, automation sandboxes, archival certification, accessible PDF tagging/remediation, citation databases/styles, multilingual typography validation, trusted signing, immutable records or device printing. These are not just hidden features awaiting credentials. Some need substantial additional implementation plus suitable runtimes/licenses/devices. The only blocked executable adapters supplied here are the two explicitly described above; other integrations are not supplied.

The source catalog's general repeated descriptions do not define precise schemas or verifiers for these specialist skills. Keep the exact original entries for requirements tracking, but do not mark a category complete merely because one operation touches it. A fully accepted implementation of the PDF would require an additional engineering/validation effort beyond this delivered component.

## Maintenance, extension and troubleshooting

Keep the folder and relative references intact. Add new deterministic handlers to the appropriate supporting module, register a clear public operation, add real CLI tests and update this guide. Do not dynamically load arbitrary plugins, execute catalog text or invent validator success. Keep external engines behind fixed validated adapters and explicit operator approval. Add certificate/signing integrations only with secure key handling and independent verification, not with embedded test credentials.

When upgrading dependencies, review licenses and security advisories, stage exact platform wheels, run the full suite and perform independent document/UI validation. Preserve source files and separate outputs. Native editing beyond the documented create/merge operations should be added with regression fixtures that demonstrate preserved parts; do not silently rewrite complex documents through a lossy text path.

A missing Python dependency is solved by approved offline provisioning, not a runtime download. Missing LibreOffice/Tesseract requires an authorized local install, approved PATH and fonts/language models; run baseline afterwards. Conversion failure commits no target file; inspect engine readiness in a quarantined environment. For layout failures, reduce content or use native Office formatting and independent rendering. For empty PDF text, confirm whether the input is scanned. For redaction uncertainty, stop release and use a qualified privacy workflow. For report/output conflicts, choose a new destination or review before approval.

The component uses no hosted resources. Hosting, catalog permissions or platform delivery failures cannot be prevented by this ZIP. Download/extract and validate locally to separate packaging defects from platform access issues. The archive is only a delivery container: extracting it immediately yields the component folder, ready to sit beside a parent's SKILL.md, not a complete skill installation.
