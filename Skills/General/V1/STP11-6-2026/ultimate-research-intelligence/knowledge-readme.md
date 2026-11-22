# Ultimate Research Intelligence — reusable Skill Tool component

This is **one component folder**, not a complete Copilot skill. Copy `ultimate-research-intelligence/` directly beside the future skill root's single `SKILL.md`. Do not put a `SKILL.md` inside this folder. Paths below are relative to that future skill root and case-sensitive.

## Purpose and capability catalog

The PDF catalog contains **35 categories and 350 named proposed skills**. Their exact names, IDs, category assignments, and descriptions are preserved in `ultimate-research-intelligence/asset-capabilities.json`; `capabilities` lists all 350, and `--category` or `--skill` selects one or more for the plan. One common governed workflow serves the catalog instead of 350 fictional provider-specific implementations. Selection records which catalog methods are in scope; it does **not** magically execute platform-only abilities.

Categories: 1 Search Strategy and Query Architecture; 2 General Web Discovery; 3 Webpage Reading, Scrolling, and Site Traversal; 4 Primary and Official Source Research; 5 Academic Literature and Study Search; 6 News, Events, and Current Affairs Research; 7 Historical and Archival Search; 8 Multilingual and Cross-Regional Search; 9 People, Organization, and Entity Research; 10 Products, Services, and Market Search; 11 Technical Documentation and Developer Search; 12 Open Source, GitHub, and Repository Search; 13 Datasets, Statistics, and Quantitative Sources; 14 Legal, Regulatory, and Policy Search; 15 Medical, Health, and Scientific Evidence Search; 16 Financial, Economic, and Business Research; 17 Social Media, Forums, and Community Search; 18 Images, Video, Audio, and Media Search; 19 Patents, Standards, and Technical Prior Art; 20 Libraries, Books, and Reference Works; 21 Local, Geographic, and Place Search; 22 Education, Courses, and Learning Resource Search; 23 Jobs, Careers, and Professional Search; 24 Procurement, Grants, and Opportunities Search; 25 Competitive Intelligence and Landscape Research; 26 Fact Checking and Claim Verification; 27 Source Quality, Bias, and Credibility Assessment; 28 Information Extraction and Structured Capture; 29 Citation, Provenance, and Research Logging; 30 Coverage, Saturation, and Missing-Information Control; 31 Synthesis, Comparison, and Interpretation; 32 Research Automation, Monitoring, and Alerts; 33 Privacy, Ethics, Rights, and Safe Search; 34 Research Quality Assurance and Verification; 35 Ultimate Integrated Search Operations.

**Implemented operations:** plan (query families and declared scope), discover (candidate links from supplied sources/feeds/sitemaps), extract (local text/HTML/JSON/CSV/XML or opted-in public HTTPS), verify (claim-to-passage candidates), audit (declared facet and source-class gaps), synthesize (citation-linked candidate findings), monitor (persistent source change detection), run (whole workflow), and capabilities (full catalog lookup). These compose for any category: e.g. select category 5 and use academic papers supplied as readable sources; select category 14 and use official legal pages; select 32 and run monitor against feeds; select 35 for integrated research. **Human review is required** for truth, legal/medical judgments, ethical decisions, source quality, contradictions, and completion. A matching phrase is not proof.

## Files and order

- `ultimate-research-intelligence/tools-main.py`: **primary CLI** and orchestration, validation, plans, evidence, audit, monitoring, structured JSON errors.
- `ultimate-research-intelligence/tools-web.py`: loaded by the primary CLI using its exact sibling filename; guarded HTTPS reader and HTML/CSV/JSON/XML/text extraction.
- `ultimate-research-intelligence/tools-test.py`: runnable temporary-fixture CLI regression tests; no permanent fixture files.
- `ultimate-research-intelligence/asset-capabilities.json`: operational catalog index extracted from the supplied PDF, used by catalog lookup and selection.
- `ultimate-research-intelligence/knowledge-readme.md`: this sole knowledge file.

No installation or dependencies beyond **Python 3.10+ standard library** for the packaged tool. PDF parsing was used only during build to prepare the catalog; runtime does not require pypdf. The tool does not parse PDF as a research source; convert a lawfully obtained PDF to UTF-8 text before passing it as a source. No API key, model, search service, browser, scheduler, or external account is bundled. Python must be on PATH.

## Exact commands (POSIX shell)

```sh
python3 ultimate-research-intelligence/tools-main.py --help
python3 ultimate-research-intelligence/tools-main.py capabilities
python3 ultimate-research-intelligence/tools-main.py capabilities --category 35
python3 ultimate-research-intelligence/tools-main.py capabilities --skill 35.10
python3 ultimate-research-intelligence/tools-main.py plan --input research.json --category 35
python3 ultimate-research-intelligence/tools-main.py discover --input research.json
python3 ultimate-research-intelligence/tools-main.py extract --input research.json --include-text
python3 ultimate-research-intelligence/tools-main.py verify --input research.json
python3 ultimate-research-intelligence/tools-main.py audit --input research.json
python3 ultimate-research-intelligence/tools-main.py synthesize --input research.json --output report.json
python3 ultimate-research-intelligence/tools-main.py monitor --input research.json --state watch-state.json
python3 ultimate-research-intelligence/tools-main.py run --input research.json --category 35
python3 ultimate-research-intelligence/tools-test.py
```

Use `--allow-network` on a research command to fetch configured public HTTPS URLs; without it, URL entries are reported as skipped. To update existing state or output, first inspect it, then add `--approve-overwrite`. No destructive writes happen without this explicit approval. Files generated by these commands are user outputs, **not** packaged resources. Source paths in user configuration resolve relative to the input JSON's directory. All packaged-resource references in this guide start with `ultimate-research-intelligence/` and resolve from the future skill root.

## Inputs, parameters, and output

`--input` is required except for `capabilities`. Example `research.json` (place `bulletin.html` alongside this JSON):

```json
{
  "question": "What changed in the solar output estimate?",
  "claims": ["Solar output was 12 units in 2024."],
  "sources": [{"path": "bulletin.html", "type": "official", "title": "Agency bulletin"}],
  "must_cover": ["methodology", "2024"],
  "source_classes": ["official", "academic"],
  "regions": ["Philippines"],
  "languages": ["English"],
  "synonyms": ["photovoltaic generation"],
  "exclude_terms": ["sales"],
  "max_pages": 12,
  "max_bytes": 1000000,
  "timeout": 10
}
```

A source has exactly one `path` or `url` and optional `type` (`primary`, `official`, `academic`, `secondary`, `community`, `other`; default `secondary`) and `title`. Supported local extensions: `.txt`, `.md`, `.html`, `.htm`, `.json`, `.csv`, `.xml`, `.rss`, `.atom`; UTF-8 recommended. Public HTTPS accepts text/html, plain text, JSON, XML, RSS, Atom, or CSV only. `max_pages` 1–50 (default 12), `max_bytes` 1024–2000000 per fetched page (default 1000000), `timeout` 1–30 seconds per HTTP request (default 10). At most 100 configured sources and 100 claims; local files max 2 MB each. The total duration is bounded by the number of sources times robots + content timeouts; cancel with Ctrl-C. `--category` accepts number or a category-name substring; `--skill` accepts exact name or catalog ID. `--output` writes the same JSON shown on stdout; `--include-text` adds extracted source text to output; `--state` is required only for monitor.

Output is JSON. All successful responses include `ok: true` and operation-specific `plan`, `sources`, `result`, `leads`, `synthesis`, or `issues`. Errors have `ok: false`, `error.code`, `error.message`, exit code 2 on stderr. `sources` have IDs, titles, types, retrieval times, hashes, provenance URLs/paths, tables, and links. `findings` retain quoted candidate passages and source IDs; `coverage` and `gaps` reveal declared missing evidence. `completion` is `incomplete` or `review required`, **never automatically verified**. `monitor` stores text hashes and bounded snapshots in JSON state; `new`, `updated` (unified diff), and `not_observed` events distinguish changes from fetch failures. Duplicates are removed by content hash. Existing monitor records are preserved if a source could not be observed on a later run.

### Practical operation examples

- **Query architecture (1), coverage (30):** `plan --input research.json --category 1` creates four base query families plus supplied synonyms/languages/regions; `audit` checks declared facets and source classes. Queries are suggestions, not automated search-engine results.
- **Web/official/academic/domain discovery (2–25):** provide lawful seed pages, feeds, sitemaps, exported records or local files; `discover` scores their links and `extract` reads their available text/tables. Inspect leads, add selected URLs to configuration, and rerun. No automatic unbounded link following.
- **Claims, credibility, structured capture, citations, synthesis, QA (26–31, 34):** `verify` finds passage candidates, `audit` reports missing evidence, and `synthesize` attaches source IDs. Reopen citations and check meaning, conflicts, currency, rights, and source independence yourself.
- **Monitoring (32):** run `monitor` once to establish baseline, then after review rerun with `--approve-overwrite`; use an external scheduler only with explicit user approval, concurrency control, and alert delivery integration if needed.
- **Privacy/ethics (33):** minimize configured personal data; public sources only; do not add private targets or use this tool for doxxing. It does not make ethical decisions automatically.
- **Integrated orchestration (35):** `run --input research.json --category 35` returns plan, provenance, gap audit and citation-backed candidates together. Follow up on gaps and rerun.

## Safety, limits and recovery

Public HTTPS is **explicit opt-in**, checks public DNS addresses and robots policy, refuses userinfo/non-443 ports, rejects redirects to disallowed hosts, imposes response and timeout bounds, and does not run downloaded code. This is a safeguard, not a network isolation guarantee against DNS rebinding or a substitute for organizational egress controls. Robots permission is not a license or terms-of-service grant; check rights and access rules. Do not put confidential data into public search or remote fetches. Input URLs are configured, not fabricated endpoints. No credentials, purchase, publishing, privileged action, or third-party execution is included. Output/state overwrite requires explicit approval; choose storage locations with suitable access controls, since source text in monitor state may contain sensitive material. For sensitive findings, review and redact before sharing. Never treat search snippets or candidate passages as verified evidence.

Not implemented: general search-engine querying, database adapters requiring keys/licensing, headless rendering/infinite scroll/interactive accordions, image/video/audio interpretation, OCR, PDF source extraction, patent/legal/medical semantic judgment, translation, citation graph APIs, automated alert delivery, scheduled jobs, and automatic truth or saturation determination. The catalog names are **a method index, not a claim that those integrations exist**. Provide source exports or extend `tools-web.py` with an approved bounded adapter; preserve provenance and test it. `--skill` scopes/labels the plan; it does not silently simulate a specialized API. No exhaustive internet coverage is promised. Cross-site freshness and competing assertions still require a human or separately approved provider integration.

For failure recovery: malformed or missing input returns structured error before processing; failed remote reads appear in `issues` without fabricating evidence. Adjust source paths, check robots/terms/network availability, rerun; monitor preserves prior unseen entries. Inspect the JSON, fix gaps, then use `--approve-overwrite` for reviewed replacements. Stop work if source terms or privacy constraints forbid collection. To extend: add narrowly scoped readers in `tools-web.py`, expose them in `tools-main.py`, and update `tools-test.py` and this file; keep remote access bounded and gated. Keep the catalog asset synchronized with actual catalog changes, and do not rename files without updating sibling imports and commands.

## Validation completed

Ran the CLI help and full/filtered capability lookup; executed plan, discover, extract (HTML/CSV), verify, audit, synthesize, monitor, and run through `tools-main.py`; tested invalid and absent inputs, no-source gaps, changed text changing citations, duplicate overwrites refused, state baseline/update/recovery, JSON output parsing, and link/table inspection using temporary fixtures. `tools-test.py` repeats these checks. No live public URL, third-party search API, browser, licensed database, or alert integration was tested; those are not claimed. Package copy/extraction checks are performed at delivery time separately from the runtime tests.
