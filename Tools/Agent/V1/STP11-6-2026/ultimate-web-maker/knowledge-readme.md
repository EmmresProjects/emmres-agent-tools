# Ultimate Web Maker Skill Tool

## Purpose and scope

This is one reusable Skill Tool component for the **Ultimate Web Maker** catalog supplied with the request. It combines all **36 categories and 360 named capabilities** in one directly copyable folder. The exact capability descriptions are bundled as data in `ultimate-web-maker/asset-capabilities.json`; the complete names are indexed at the end of this file.

The component provides four practical local behaviors:

1. **Catalog routing** — list, search, and show every named capability; resolve one name, a numbered list, one category, or several categories from plain text, Markdown, or copied HTML.
2. **Deterministic planning** — turn a brief into a traceable project plan with a delivery recommendation, phases, open questions, risk notes, approval gates, and relevant catalog categories.
3. **Static starter generation** — create a new, no-build HTML/CSS/JavaScript website directory from a brief using editable templates and one of the bundled design profiles.
4. **Bounded local source audit** — inspect readable local project files for selected HTML semantics, accessibility, link, security, responsive, SEO, PWA, media, and performance heuristics, without executing project code or contacting a network.

The 360 capabilities are available through `run`. Category-specific workflows produce checklists, a plan snapshot, design tokens, or applicable static audit findings. Provider-dependent capabilities (for example, production payments, cloud provisioning, framework builds, DNS changes, real documentation research, or browser/device testing) produce requirements and review checklists—not fabricated integrations or claims of completed work.

## Files and execution order

All files are directly inside the `ultimate-web-maker/` Skill Tool folder; there are no internal subfolders.

- `ultimate-web-maker/tools-webmaker.py` — **primary runnable CLI entry point**. It parses commands and emits reports or structured errors.
- `ultimate-web-maker/tools-engine.py` — reusable planning, catalog, brief/selection parsing, configuration, output, and scaffold orchestration loaded by the entry point.
- `ultimate-web-maker/tools-static-audit.py` — isolated bounded source scanner and HTML/CSS/JavaScript heuristic analyzer loaded by the engine for `audit`, applicable `run` workflows, and scaffold self-checks.
- `ultimate-web-maker/asset-capabilities.json` — canonical catalog data: 36 categories, 360 names, category summaries, and descriptions.
- `ultimate-web-maker/asset-default-config.json` — safe limits, scanner policy, design profiles, approval policy, and one functional workflow for each catalog category. A partial user JSON config can override supported settings.
- `ultimate-web-maker/asset-project-brief-template.md` — input template emitted by the `template` command.
- `ultimate-web-maker/asset-index-template.html`, `ultimate-web-maker/asset-styles-template.css`, and `ultimate-web-maker/asset-app-template.js` — editable, separately maintained HTML, CSS, and JavaScript templates used by `scaffold`.
- `ultimate-web-maker/asset-site-readme-template.md` — run and handoff guidance generated alongside a static starter.
- `ultimate-web-maker/knowledge-readme.md` — this single knowledge file.

The primary entry point loads `tools-engine.py` from its own folder. The engine then loads `tools-static-audit.py` and the `asset-*` resources from that same folder, so the current shell directory does not affect internal resource lookup. User-supplied paths are interpreted from the current shell directory.

## Setup and dependencies

- Runtime: **Python 3.10 or newer**. The implementation uses only the Python standard library; there is no package installation, network access, compiler, browser automation, or third-party dependency.
- Validation performed for this package used Python **3.13.16**. Other Python versions in the stated range were not separately executed here.
- Keep every `tools-*` and `asset-*` file together in `ultimate-web-maker/`. Copy that folder unchanged beside the parent package's single root `SKILL.md`.
- Commands below are for a **POSIX shell** (`sh` or `bash`) from the future skill root (the directory containing `SKILL.md` and `ultimate-web-maker/`).

```sh
python3 ultimate-web-maker/tools-webmaker.py --help
python3 ultimate-web-maker/tools-webmaker.py doctor
python3 ultimate-web-maker/tools-webmaker.py capabilities list --format md
```

When working from inside the component folder instead, use `python3 tools-webmaker.py ...`. Do not run generated project commands from inside `ultimate-web-maker/`; use a separate project directory. The CLI intentionally refuses report or scaffold outputs inside the packaged component folder to protect the shipped files.

## Commands and examples

Every command and nested command has built-in `--help`. `doctor` verifies the local catalog, workflow map, and template references; it does not contact external services.

### 1. Capability catalog

```sh
python3 ultimate-web-maker/tools-webmaker.py capabilities list --format md
python3 ultimate-web-maker/tools-webmaker.py capabilities list --format json --output catalog.json
python3 ultimate-web-maker/tools-webmaker.py capabilities search "accessibility keyboard" --format json
python3 ultimate-web-maker/tools-webmaker.py capabilities show "WCAG Accessibility Engineer" --format md
python3 ultimate-web-maker/tools-webmaker.py capabilities show "Category 9" --format md
```

`list-capabilities` is an alias for `capabilities list`. Search matches every query word against category names, capability names, and descriptions. `show` accepts an exact or unique partial category/capability selector. The catalog output uses `category_count`, `capability_count`, `categories`, and per-capability `name`/`description` fields.

### 2. Project brief and planning

Create a starter brief, edit it, then produce JSON (the default) or Markdown:

```sh
python3 ultimate-web-maker/tools-webmaker.py template --output brief.md
python3 ultimate-web-maker/tools-webmaker.py plan --brief brief.md --output plan.json
python3 ultimate-web-maker/tools-webmaker.py plan --text "Create a warm, mobile-first site that helps local customers find a bakery menu and opening hours." --format md
```

Brief inputs may be UTF-8 plain text, Markdown, copied HTML, or a JSON object. HTML tags and attributes are treated as markup; script/style bodies are ignored. The planner does not execute supplied text. Recognized labels/JSON keys include `title`/`project`, `goal`/`purpose`/`description`, `audience`/`users`, `features`, `content`/`pages`, `stack`/`framework`/`runtime`, `integrations`/`apis`, `hosting`/`deployment`, `constraints`, `success_criteria`/`acceptance_checks`, and `language`/`locale`. Unknown JSON keys are ignored. Missing details become open questions or explicit assumptions, not invented facts.

The JSON plan includes `project`, `recommendation`, `phases`, `open_questions`, `risks`, `approval_gates`, `recommended_categories`, and `limitations`. It is a planning artifact, not source code or an implementation status claim. Version status remains `not-verified`; this offline tool does not research current documentation.

### 3. Capability selection and execution

A selection can be inline or read from a file. A category selection expands to all ten named capabilities in that category. Multiple selected tools in one category share that category's workflow and preserve each selected name and description in the output.

```sh
python3 ultimate-web-maker/tools-webmaker.py run --selection "WCAG Accessibility Engineer" --project ./existing-site --format json
python3 ultimate-web-maker/tools-webmaker.py run --selection-file selection.html --brief brief.md --project ./existing-site --output work-report.md --format md
python3 ultimate-web-maker/tools-webmaker.py run --selection "Category 1: Web Strategy, Discovery, and Product Planning" --brief brief.md --format json
```

`--selection`/`--capability` and `--selection-file` accept one skill, numbered skill list, one category, or several categories in plain text, Markdown, or copied HTML. Tag names, CSS classes, data attributes, numbering/bullets, and excess spacing do not affect matching. Use full category titles or `Category N` to avoid ambiguous short names. `run` requires a brief, a project directory, or both.

Category workflow classes are deliberately explicit:

- **Planning/checklist workflows:** categories 1, 7, 11–23, 28, 30, 31, 34, and 35. They produce category-specific deliverables and review checks; they do not call live providers or mutate a repository.
- **Offline research workflow:** category 2. It produces a version/documentation verification checklist and clearly reports that no official source was queried.
- **Design workflow:** category 3. It returns the selected design tokens from the configured profile plus the category's design checklist. Tokens are starting points, not an approved brand system.
- **Static audit workflows:** categories 4–6, 8–10, 24–27, 29, 32, and 33. With `--project`, `run` filters the bounded local audit to relevant categories; without it, it returns the manual review checklist and states that source findings were not run. Category 33 spans the static checks implemented here.
- **Orchestration workflow:** category 36. With a brief it includes the deterministic plan; with a project it includes the audit summary when applicable. Use `scaffold` and `audit` explicitly for the supported static vertical-slice path.

Per-category `deliverables`, `checks`, and audit tags are editable in `ultimate-web-maker/asset-default-config.json`; per-skill descriptions remain in `ultimate-web-maker/asset-capabilities.json`.

### 4. Static website scaffold

```sh
python3 ultimate-web-maker/tools-webmaker.py scaffold --brief brief.md --output-dir ./new-site --profile editorial
python3 ultimate-web-maker/tools-webmaker.py audit --project ./new-site --format md
```

The command creates exactly these editable files in a **new, nonexistent** directory: `index.html`, `styles.css`, `app.js`, and `README.md`. The starter has no package manager or build step; JavaScript only controls the small-screen navigation, and the content remains readable if JavaScript is unavailable. Design profiles are `editorial`, `studio`, and `minimal` by default; custom profiles can be added in a user config with validated color/font tokens.

`scaffold` is intentionally static-only. If a brief specifies another stack, it refuses to silently misrepresent the result. `--allow-static-prototype` explicitly permits a separate HTML prototype; it still does not build the requested framework. The template copy is English. A brief explicitly labeled with a non-English language requires `--allow-english-copy`; the resulting document is still labeled `en`, and the warning/README require translation and correct language metadata before use. There is no automatic translation.

To preview the generated site, enter its project directory and use a **POSIX shell**:

```sh
python3 -m http.server 8000
```

Then open `http://localhost:8000` in a browser. Stop the local server with `Ctrl-C`. This is a local preview command for the generated static site, not a command the Skill Tool itself runs.

### 5. Static project audit

```sh
python3 ultimate-web-maker/tools-webmaker.py audit --project ./existing-site --format json
python3 ultimate-web-maker/tools-webmaker.py audit --project ./existing-site --fail-on none --format md
```

Audit output includes `scan` (file/byte limits, completeness, warnings), `summary` (severity/category counts), `findings` (stable rule ID, severity, category, relative path, line, message, recommendation), and `limitations`. Default quality gate is `high`: exit status is 1 when a high/critical finding exists; use `--fail-on` to set `none`, `critical`, `high`, `medium`, `low`, or `info`. An audit can complete successfully while reporting findings. Audit paths in findings are relative to the scanned project.

Implemented checks include: HTML title/description/viewport/lang/main/heading/duplicate-ID and common label/name checks; local file/fragment links and root escapes; HTTP-loaded resources and inline event attributes; common dynamic-JavaScript/HTML-sink patterns; likely credential/private-key files (contents are not read) and redacted credential-like strings; CSS focus/responsive/reduced-motion checks; manifest JSON/name checks; and simple local media-size warnings. See the rule IDs/messages in `ultimate-web-maker/tools-static-audit.py` for the exact current implementation. It does not parse every framework/template language and does not measure rendered behavior.

### 6. Report outputs and overwrite behavior

`plan`, `run`, `audit`, catalog commands, `doctor`, and `scaffold` accept configurable output paths where applicable. JSON is the default for operation reports; `--format md` creates human-readable Markdown. Output parents are created as needed. Existing report/template files are never overwritten unless `--force` is supplied; that flag is the explicit user approval for replacing that output file. Input files are protected from being selected as report outputs. The scaffold directory is never overwritten, even with `--force`. Outputs inside the Skill Tool folder are refused.

## Configuration and interfaces

### Partial user configuration

Pass `--config PATH` to `plan`, `run`, `scaffold`, `audit`, or `doctor`. Configuration is UTF-8 JSON with `schema_version: 1`; it may partially override the documented top-level sections `limits`, `scanner`, `profiles`, `approval_policy`, and `workflows`. Unknown setting names, malformed values, unsafe font/color tokens, and unsupported workflow kinds fail before work starts. No configuration field is executable code or a provider credential.

Default bounds in `ultimate-web-maker/asset-default-config.json`:

| Setting | Default | Hard maximum |
|---|---:|---:|
| `max_brief_bytes` | 1 MiB | 5 MiB |
| `max_selection_bytes` | 256 KiB | 1 MiB |
| `max_config_bytes` | 1 MiB | 2 MiB |
| `max_project_files` | 2,500 | 10,000 |
| `max_project_entries` (files and directories enumerated) | 10,000 | 50,000 |
| `max_file_bytes` | 1 MiB | 5 MiB |
| `max_total_bytes` | 32 MiB | 100,000,000 bytes |
| `max_depth` | 12 | 24 |
| `max_findings` | 500 | 2,000 |
| `operation_timeout_seconds` | 90 seconds | 600 seconds |

The scan skips configured dependency/build/cache directories, never follows symlinks, and only reads configured text extensions. Credential-bearing filenames such as `.env` are flagged without reading their contents. Binary, non-UTF-8, oversized, excluded, or over-budget content may be omitted; the report marks a partial scan when applicable. Increase limits only when needed and within the hard maximum.

### Input/output contracts

- **Brief input:** UTF-8 file (`--brief`), stdin (`--brief -`), or inline text (`--text`). Use one source, not both. Supported forms: prose, Markdown, copied HTML, or a JSON object.
- **Selection input:** inline `--selection`/`--capability` or `--selection-file`; supports plain/Markdown/HTML names and category lists. The catalog itself is a fixed bundled JSON asset, not imported from arbitrary archives at runtime.
- **Project input:** existing directory path. Symlink roots are refused; symlink entries are not followed.
- **Report output:** stdout unless `--output` is given; `--format json|md`; atomic single-file write with a same-directory temporary file; explicit `--force` for report replacement.
- **Scaffold output:** a new directory path; four files are staged and statically audited before publication, created with exclusive file creation, and never replace existing files.
- **Structured errors:** failures go to stderr as a JSON object with `ok: false` and `error.code`, `error.message`, and, when useful, `error.hint`. Invalid input returns nonzero status; an audit quality-gate failure returns status 1; cancellation returns 130.

## Safety, timeouts, retries, cancellation, and recovery

- The CLI does not execute brief text, project code, shell strings, build scripts, or tool commands. It does not open network connections or invoke external services.
- There is no model/provider adapter, prompt execution, browser automation, package installation, compiler, database, hosting, CMS, identity provider, payment processor, DNS, cloud, or deployment integration in this component. Provider/model settings are therefore not applicable; integrations are represented as planning requirements only. There is no internal persistent store: reports are retained only when the caller chooses an output path.
- Any future publish/deploy/DNS, live-payment, credential, production-data, destructive-overwrite, or privileged security action requires separate human approval. This implementation does not perform those actions.
- Report replacement needs explicit `--force`; scaffold creation refuses an existing target. The package folder itself cannot be an output target.
- Audit reads are bounded by the configured file/byte/depth/finding limits and operation deadline. Default timeout is 90 seconds, configurable up to 600 seconds. There are no retries because there are no network or provider calls.
- `Ctrl-C` cancels the current process (status 130). Scaffold staging is removed in a `finally` cleanup; if publication fails, only files exclusively created by that attempt are removed. Report writes use a temporary sibling file and atomic replace; replacing an existing report requires the explicit `--force` approval.
- If an operation times out or a scan is partial, narrow the project path or exclusions, or increase a limit within its maximum. If output exists, choose a new name; if a scaffold target exists, preserve it and choose a new directory. On a failed scaffold, inspect the destination before retrying.

## Validation and tests performed

Validation performed against the final tool behavior on Python 3.13.16 in the supplied environment:

- `python3 -m py_compile tools-webmaker.py tools-engine.py tools-static-audit.py` — passed.
- `python3 tools-webmaker.py --help` — passed; help lists all commands.
- `python3 tools-webmaker.py doctor` — passed; 36 categories, 360 capabilities, 36 workflows, and all seven assets checked.
- `capabilities list --format json`, `search`, `show`, and the version/help commands ran; JSON catalog output parsed with 360 unique names and non-empty per-tool descriptions.
- Plain/Markdown, JSON-object, and copied-HTML briefs were tested. HTML tags/attributes and script text did not enter generated content; numbered Markdown capability selection and copied-HTML multi-category selection resolved as expected.
- A valid static brief produced a structured plan and a four-file scaffold. The generated project audit completed with zero findings; generated `app.js` passed `node --check` in the environment (Node.js is not a package dependency).
- A changed brief and `studio` profile changed the generated title, summary, and design tokens. A partial custom JSON config changed a profile token successfully; a restrictive scan config produced a marked partial result.
- A deliberately unsafe fixture reported missing language/labels/alt and local assets, JavaScript `eval`, an HTML-string sink, an HTTP asset, a likely secret assignment, and a `.env` filename. Neither `.env` contents nor the credential-like value appeared in the report; the configured high-severity gate failed as expected.
- Missing brief, malformed JSON, unknown capability, missing project, unknown config, unsupported stack/language, output collision, overwrite without approval, and existing scaffold target returned structured errors with nonzero status. Explicit `--force` report replacement succeeded; static-prototype and English-copy opt-ins emitted warnings.
- Design, offline documentation-research, planning, and audit workflow outputs were checked. A selected accessibility category resolved to all ten tools and produced category-filtered project findings.

- Packaging checks passed: ZIP integrity and direct `ultimate-web-maker/` root membership were verified; extraction produced the component immediately with 11 files, no wrapper folder, no nested archive, and no `SKILL.md` inside the component. A clean parent root with one test `SKILL.md` and a separately extracted ZIP root both ran the primary CLI using relative paths; help, doctor, capability listing, planning, scaffolding, and audit passed, the generated static site audit had zero findings, and its JavaScript passed `node --check`. The clean-copy root also generated the brief template. Neither tested component copy created a runtime cache folder.

Browser rendering, framework builds, platform-specific deployments, external documentation lookups, and the full cross-browser/device matrix are not claimed as tested.

## Troubleshooting, recovery, extension, and maintenance

- **Missing asset/tool file:** restore all exact-case filenames from the ZIP directly into `ultimate-web-maker/`. Keep the three `tools-*` files and all required `asset-*` files beside one another.
- **Unknown capability:** run `capabilities search "words"`; use the exact name or full `Category N` selector. A category expands to its ten catalog entries.
- **Invalid brief/config:** use UTF-8; JSON briefs must be objects; configs must use schema version 1 and documented keys. Error details identify the field/line or remediation hint.
- **No source findings:** `run` needs `--project` for static checks; unsupported framework sources may only yield manual checklists. `audit` never installs or invokes framework tooling.
- **Partial audit:** review `scan.warnings`; increase limits within the hard maxima or configure skip directories/extensions. Do not interpret partial results as a clean pass.
- **Scaffold rejected:** use a new output directory. A non-static brief requires `--allow-static-prototype` to request a separate static mockup; non-English briefs require explicit `--allow-english-copy` and still need translation.
- **Extending categories:** add category/capability records with unique IDs/names and descriptions in `asset-capabilities.json`, then add a workflow entry with an allowed kind and checks in `asset-default-config.json`. Keep the catalog count metadata synchronized; `doctor` verifies the mapping. Add an audit tag only if corresponding findings are actually implemented.
- **Extending audit rules:** modify `tools-static-audit.py`, assign stable rule IDs and category names, never include secret values in findings, preserve symlink/network/code-execution boundaries, and add a real fixture validation before documenting the rule.
- **Changing scaffold:** edit the distinct asset templates, keep token names in the renderer allowlist, preserve semantic HTML/keyboard/focus/reduced-motion behavior, and rerun `doctor`, `scaffold`, and `audit` from a clean output directory.
- **Version maintenance:** Python runtime compatibility, web standards, framework behavior, and browser support can change. Check official docs and actual target runtimes before adding claims or new supported integrations. This static component does not fetch live version intelligence.

## Complete capability index

The exact descriptions for these names are in `ultimate-web-maker/asset-capabilities.json`. Each bullet includes a runnable `run` example; the category workflow descriptions above explain the shared behavior, and `run` returns the selected capability's exact description. Selecting a category runs all ten entries through that category's workflow. Examples are for a POSIX shell from the future skill root.

### 1. Web Strategy, Discovery, and Product Planning
- **Web Creative Director** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Creative Director" --brief brief.md --format json`
- **Website Concept Generator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Website Concept Generator" --brief brief.md --format json`
- **Product Requirements Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Product Requirements Architect" --brief brief.md --format json`
- **User Journey Mapper** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "User Journey Mapper" --brief brief.md --format json`
- **Feature Scope Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Feature Scope Planner" --brief brief.md --format json`
- **Website Type and Stack Router** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Website Type and Stack Router" --brief brief.md --format json`
- **Technical Feasibility Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Technical Feasibility Analyst" --brief brief.md --format json`
- **Content and Asset Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Content and Asset Planner" --brief brief.md --format json`
- **Delivery Roadmap Builder** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Delivery Roadmap Builder" --brief brief.md --format json`
- **End-to-End Web Producer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "End-to-End Web Producer" --brief brief.md --format json`

### 2. Standards, Documentation, and Version Intelligence
- **Web Standards Researcher** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Standards Researcher" --brief brief.md --format json`
- **Official Documentation Researcher** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Official Documentation Researcher" --brief brief.md --format json`
- **Browser Compatibility Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Browser Compatibility Analyst" --brief brief.md --format json`
- **Framework Version Compatibility Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Framework Version Compatibility Analyst" --brief brief.md --format json`
- **Legacy Web Technology Archivist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Legacy Web Technology Archivist" --brief brief.md --format json`
- **API and Syntax Diff Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "API and Syntax Diff Analyst" --brief brief.md --format json`
- **Dependency Deprecation Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Dependency Deprecation Planner" --brief brief.md --format json`
- **Migration Path Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Migration Path Designer" --brief brief.md --format json`
- **Standards Compliance Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Standards Compliance Reviewer" --brief brief.md --format json`
- **Reproducible Web Environment Builder** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Reproducible Web Environment Builder" --brief brief.md --format json`

### 3. Visual Direction, Branding, and Creative Web Design
- **Web Art Director** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Art Director" --brief brief.md --format json`
- **Creative Layout Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Creative Layout Designer" --brief brief.md --format json`
- **Brand-to-Web System Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Brand-to-Web System Designer" --brief brief.md --format json`
- **Color System Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Color System Designer" --brief brief.md --format json`
- **Typography System Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Typography System Designer" --brief brief.md --format json`
- **Visual Hierarchy Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Visual Hierarchy Specialist" --brief brief.md --format json`
- **Editorial Web Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Editorial Web Designer" --brief brief.md --format json`
- **Experimental Web Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Experimental Web Designer" --brief brief.md --format json`
- **Campaign Microsite Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Campaign Microsite Designer" --brief brief.md --format json`
- **Design Originality Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Design Originality Reviewer" --brief brief.md --format json`

### 4. Responsive Layout, CSS, and Design Systems
- **Responsive Layout Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Responsive Layout Architect" --project ./existing-site --format json`
- **CSS Architecture Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "CSS Architecture Engineer" --project ./existing-site --format json`
- **CSS Grid Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "CSS Grid Specialist" --project ./existing-site --format json`
- **Flexbox Layout Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Flexbox Layout Specialist" --project ./existing-site --format json`
- **Container Query Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Container Query Developer" --project ./existing-site --format json`
- **Responsive Typography Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Responsive Typography Engineer" --project ./existing-site --format json`
- **Design Token Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Design Token Architect" --project ./existing-site --format json`
- **Component Style System Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Component Style System Developer" --project ./existing-site --format json`
- **Dark Mode and Theme Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Dark Mode and Theme Engineer" --project ./existing-site --format json`
- **Cross-Browser CSS Debugger** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cross-Browser CSS Debugger" --project ./existing-site --format json`

### 5. HTML, Semantic Markup, and Document Structure
- **HTML5 Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "HTML5 Application Developer" --project ./existing-site --format json`
- **Semantic HTML Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Semantic HTML Architect" --project ./existing-site --format json`
- **Legacy HTML Compatibility Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Legacy HTML Compatibility Specialist" --project ./existing-site --format json`
- **Structured Data Markup Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Structured Data Markup Developer" --project ./existing-site --format json`
- **Forms and Validation Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Forms and Validation Developer" --project ./existing-site --format json`
- **Media Embedding Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Media Embedding Specialist" --project ./existing-site --format json`
- **Web Components Markup Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Components Markup Designer" --project ./existing-site --format json`
- **Document Outline Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Document Outline Reviewer" --project ./existing-site --format json`
- **Metadata and Social Card Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Metadata and Social Card Developer" --project ./existing-site --format json`
- **HTML Conformance Validator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "HTML Conformance Validator" --project ./existing-site --format json`

### 6. JavaScript, TypeScript, and Browser Programming
- **JavaScript Application Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "JavaScript Application Architect" --project ./existing-site --format json`
- **TypeScript Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "TypeScript Application Developer" --project ./existing-site --format json`
- **DOM and Event Systems Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "DOM and Event Systems Engineer" --project ./existing-site --format json`
- **Asynchronous JavaScript Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Asynchronous JavaScript Specialist" --project ./existing-site --format json`
- **Web Worker Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Worker Developer" --project ./existing-site --format json`
- **Service Worker Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Service Worker Developer" --project ./existing-site --format json`
- **Web Storage Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Storage Engineer" --project ./existing-site --format json`
- **Module and Bundling Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Module and Bundling Specialist" --project ./existing-site --format json`
- **Browser API Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Browser API Integrator" --project ./existing-site --format json`
- **JavaScript Runtime Debugger** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "JavaScript Runtime Debugger" --project ./existing-site --format json`

### 7. Frontend Frameworks and Meta-Frameworks
- **React Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "React Application Developer" --brief brief.md --format json`
- **Next.js Full-Stack Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Next.js Full-Stack Developer" --brief brief.md --format json`
- **Vue Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Vue Application Developer" --brief brief.md --format json`
- **Nuxt Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Nuxt Developer" --brief brief.md --format json`
- **Angular Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Angular Application Developer" --brief brief.md --format json`
- **Svelte and SvelteKit Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Svelte and SvelteKit Developer" --brief brief.md --format json`
- **SolidJS Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SolidJS Developer" --brief brief.md --format json`
- **Qwik Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Qwik Developer" --brief brief.md --format json`
- **Astro Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Astro Developer" --brief brief.md --format json`
- **Framework Migration Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Framework Migration Specialist" --brief brief.md --format json`

### 8. UI Components, Interactions, Effects, and Motion
- **Interactive Component Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Interactive Component Designer" --project ./existing-site --format json`
- **Button and Control Interaction Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Button and Control Interaction Developer" --project ./existing-site --format json`
- **Microinteraction Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Microinteraction Designer" --project ./existing-site --format json`
- **Page Transition Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Page Transition Developer" --project ./existing-site --format json`
- **Scroll Animation Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Scroll Animation Engineer" --project ./existing-site --format json`
- **Text Animation and Kinetic Type Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Text Animation and Kinetic Type Developer" --project ./existing-site --format json`
- **SVG Animation Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SVG Animation Developer" --project ./existing-site --format json`
- **Canvas Effects Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Canvas Effects Developer" --project ./existing-site --format json`
- **WebGL Visual Effects Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WebGL Visual Effects Developer" --project ./existing-site --format json`
- **Reduced-Motion and Motion Safety Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Reduced-Motion and Motion Safety Reviewer" --project ./existing-site --format json`

### 9. Accessibility and Inclusive Web Engineering
- **WCAG Accessibility Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WCAG Accessibility Engineer" --project ./existing-site --format json`
- **Keyboard Navigation Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Keyboard Navigation Developer" --project ./existing-site --format json`
- **Screen Reader Compatibility Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Screen Reader Compatibility Specialist" --project ./existing-site --format json`
- **Focus Management Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Focus Management Engineer" --project ./existing-site --format json`
- **Accessible Forms Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Accessible Forms Specialist" --project ./existing-site --format json`
- **Color and Contrast Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Color and Contrast Reviewer" --project ./existing-site --format json`
- **Accessible Rich Internet Applications Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Accessible Rich Internet Applications Specialist" --project ./existing-site --format json`
- **Caption and Transcript Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Caption and Transcript Integrator" --project ./existing-site --format json`
- **Cognitive Accessibility Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cognitive Accessibility Reviewer" --project ./existing-site --format json`
- **Accessibility Regression Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Accessibility Regression Tester" --project ./existing-site --format json`

### 10. Information Architecture, Navigation, and Search
- **Information Architecture Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Information Architecture Designer" --project ./existing-site --format json`
- **Navigation System Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Navigation System Architect" --project ./existing-site --format json`
- **Mega Menu Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Mega Menu Developer" --project ./existing-site --format json`
- **Breadcrumb and Wayfinding Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Breadcrumb and Wayfinding Developer" --project ./existing-site --format json`
- **Site Search Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Site Search Engineer" --project ./existing-site --format json`
- **Faceted Search Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Faceted Search Developer" --project ./existing-site --format json`
- **Filtering and Sorting Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Filtering and Sorting Engineer" --project ./existing-site --format json`
- **Taxonomy and Tagging Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Taxonomy and Tagging Designer" --project ./existing-site --format json`
- **URL and Routing Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "URL and Routing Architect" --project ./existing-site --format json`
- **Information Findability Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Information Findability Analyst" --project ./existing-site --format json`

### 11. Content Management Systems and Publishing
- **WordPress Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WordPress Developer" --brief brief.md --format json`
- **Drupal Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Drupal Developer" --brief brief.md --format json`
- **Joomla Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Joomla Developer" --brief brief.md --format json`
- **Headless CMS Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Headless CMS Architect" --brief brief.md --format json`
- **Contentful Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Contentful Integrator" --brief brief.md --format json`
- **Sanity Studio Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Sanity Studio Developer" --brief brief.md --format json`
- **Strapi Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Strapi Developer" --brief brief.md --format json`
- **Directus Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Directus Developer" --brief brief.md --format json`
- **Editorial Workflow Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Editorial Workflow Designer" --brief brief.md --format json`
- **CMS Migration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "CMS Migration Engineer" --brief brief.md --format json`

### 12. E-Commerce, Shops, Stores, and Payments
- **E-Commerce Solution Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "E-Commerce Solution Architect" --brief brief.md --format json`
- **Shopify Theme and App Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Shopify Theme and App Developer" --brief brief.md --format json`
- **WooCommerce Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WooCommerce Developer" --brief brief.md --format json`
- **Headless Commerce Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Headless Commerce Developer" --brief brief.md --format json`
- **Product Catalog Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Product Catalog Engineer" --brief brief.md --format json`
- **Cart and Checkout Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cart and Checkout Developer" --brief brief.md --format json`
- **Payment Gateway Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Payment Gateway Integrator" --brief brief.md --format json`
- **Order and Fulfillment Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Order and Fulfillment Engineer" --brief brief.md --format json`
- **Tax Shipping and Pricing Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Tax Shipping and Pricing Integrator" --brief brief.md --format json`
- **Commerce Fraud and Reliability Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Commerce Fraud and Reliability Reviewer" --brief brief.md --format json`

### 13. Customer Accounts, Identity, and Authentication
- **Identity Architecture Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Identity Architecture Engineer" --brief brief.md --format json`
- **Email and Password Authentication Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Email and Password Authentication Developer" --brief brief.md --format json`
- **Passwordless and Passkey Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Passwordless and Passkey Developer" --brief brief.md --format json`
- **OAuth and OpenID Connect Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "OAuth and OpenID Connect Integrator" --brief brief.md --format json`
- **Single Sign-On Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Single Sign-On Engineer" --brief brief.md --format json`
- **Multi-Factor Authentication Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Multi-Factor Authentication Developer" --brief brief.md --format json`
- **Session and Token Security Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Session and Token Security Engineer" --brief brief.md --format json`
- **Role and Permission Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Role and Permission Designer" --brief brief.md --format json`
- **Account Recovery Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Account Recovery Developer" --brief brief.md --format json`
- **Identity Migration Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Identity Migration Specialist" --brief brief.md --format json`

### 14. Customer Support, CRM, and Communication
- **Customer Support Portal Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Customer Support Portal Developer" --brief brief.md --format json`
- **Help Center and Knowledge Base Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Help Center and Knowledge Base Designer" --brief brief.md --format json`
- **Live Chat Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Live Chat Integrator" --brief brief.md --format json`
- **Ticketing System Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Ticketing System Integrator" --brief brief.md --format json`
- **CRM Integration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "CRM Integration Engineer" --brief brief.md --format json`
- **Contact and Inquiry Workflow Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Contact and Inquiry Workflow Developer" --brief brief.md --format json`
- **Email Notification Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Email Notification Engineer" --brief brief.md --format json`
- **SMS and Messaging Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SMS and Messaging Integrator" --brief brief.md --format json`
- **Chatbot and Agent Handoff Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Chatbot and Agent Handoff Designer" --brief brief.md --format json`
- **Support Analytics Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Support Analytics Developer" --brief brief.md --format json`

### 15. Backend APIs and Server Applications
- **Node.js Backend Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Node.js Backend Developer" --brief brief.md --format json`
- **Express.js API Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Express.js API Developer" --brief brief.md --format json`
- **NestJS Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "NestJS Developer" --brief brief.md --format json`
- **Python Web Backend Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Python Web Backend Developer" --brief brief.md --format json`
- **Django Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Django Developer" --brief brief.md --format json`
- **Flask and FastAPI Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Flask and FastAPI Developer" --brief brief.md --format json`
- **PHP and Laravel Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "PHP and Laravel Developer" --brief brief.md --format json`
- **Ruby on Rails Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Ruby on Rails Developer" --brief brief.md --format json`
- **Java and Spring Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Java and Spring Developer" --brief brief.md --format json`
- **.NET Web API Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection ".NET Web API Developer" --brief brief.md --format json`

### 16. Additional Backend Languages and Runtimes
- **Go Web Service Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Go Web Service Developer" --brief brief.md --format json`
- **Rust Web Backend Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Rust Web Backend Developer" --brief brief.md --format json`
- **Elixir and Phoenix Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Elixir and Phoenix Developer" --brief brief.md --format json`
- **Kotlin Server Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Kotlin Server Developer" --brief brief.md --format json`
- **Scala Web Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Scala Web Developer" --brief brief.md --format json`
- **C++ Web Service Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "C++ Web Service Developer" --brief brief.md --format json`
- **Bun Runtime Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Bun Runtime Developer" --brief brief.md --format json`
- **Deno Runtime Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Deno Runtime Developer" --brief brief.md --format json`
- **Serverless Runtime Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Serverless Runtime Developer" --brief brief.md --format json`
- **Backend Runtime Migration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Backend Runtime Migration Engineer" --brief brief.md --format json`

### 17. API Design, Integration, and Automation
- **REST API Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "REST API Architect" --brief brief.md --format json`
- **GraphQL API Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "GraphQL API Developer" --brief brief.md --format json`
- **gRPC Service Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "gRPC Service Developer" --brief brief.md --format json`
- **WebSocket Realtime Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WebSocket Realtime Developer" --brief brief.md --format json`
- **Webhook Systems Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Webhook Systems Engineer" --brief brief.md --format json`
- **Third-Party API Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Third-Party API Integrator" --brief brief.md --format json`
- **API Gateway Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "API Gateway Engineer" --brief brief.md --format json`
- **API Versioning Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "API Versioning Specialist" --brief brief.md --format json`
- **OpenAPI Documentation Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "OpenAPI Documentation Developer" --brief brief.md --format json`
- **Integration Reliability Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Integration Reliability Tester" --brief brief.md --format json`

### 18. Databases, Data Modeling, and Persistence
- **Relational Database Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Relational Database Architect" --brief brief.md --format json`
- **PostgreSQL Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "PostgreSQL Developer" --brief brief.md --format json`
- **MySQL and MariaDB Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "MySQL and MariaDB Developer" --brief brief.md --format json`
- **SQL Server Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SQL Server Developer" --brief brief.md --format json`
- **SQLite Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SQLite Application Developer" --brief brief.md --format json`
- **MongoDB Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "MongoDB Developer" --brief brief.md --format json`
- **Redis Data Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Redis Data Engineer" --brief brief.md --format json`
- **Document and Key-Value Data Modeler** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Document and Key-Value Data Modeler" --brief brief.md --format json`
- **Database Migration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Database Migration Engineer" --brief brief.md --format json`
- **Data Integrity and Query Performance Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Data Integrity and Query Performance Reviewer" --brief brief.md --format json`

### 19. Realtime, Messaging, Queues, and Event Systems
- **Realtime Application Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Realtime Application Architect" --brief brief.md --format json`
- **WebSocket Scaling Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WebSocket Scaling Engineer" --brief brief.md --format json`
- **Server-Sent Events Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Server-Sent Events Developer" --brief brief.md --format json`
- **Message Queue Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Message Queue Integrator" --brief brief.md --format json`
- **Event-Driven System Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Event-Driven System Designer" --brief brief.md --format json`
- **Kafka Integration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Kafka Integration Engineer" --brief brief.md --format json`
- **RabbitMQ Integration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "RabbitMQ Integration Engineer" --brief brief.md --format json`
- **Background Job Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Background Job Developer" --brief brief.md --format json`
- **Notification Fanout Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Notification Fanout Engineer" --brief brief.md --format json`
- **Event Ordering and Idempotency Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Event Ordering and Idempotency Reviewer" --brief brief.md --format json`

### 20. Cloud Architecture and Provider Operations
- **Cloud Solution Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cloud Solution Architect" --brief brief.md --format json`
- **AWS Web Platform Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "AWS Web Platform Engineer" --brief brief.md --format json`
- **Microsoft Azure Web Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Microsoft Azure Web Engineer" --brief brief.md --format json`
- **Google Cloud Web Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Google Cloud Web Engineer" --brief brief.md --format json`
- **Cloudflare Platform Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cloudflare Platform Developer" --brief brief.md --format json`
- **DigitalOcean Platform Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "DigitalOcean Platform Engineer" --brief brief.md --format json`
- **Oracle Cloud Web Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Oracle Cloud Web Engineer" --brief brief.md --format json`
- **Multi-Cloud Architecture Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Multi-Cloud Architecture Designer" --brief brief.md --format json`
- **Hybrid Cloud Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Hybrid Cloud Integrator" --brief brief.md --format json`
- **Cloud Migration Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cloud Migration Planner" --brief brief.md --format json`

### 21. Hosting, Domains, DNS, CDN, and Edge Delivery
- **Web Hosting Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Hosting Architect" --brief brief.md --format json`
- **Domain and Registrar Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Domain and Registrar Manager" --brief brief.md --format json`
- **DNS Configuration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "DNS Configuration Engineer" --brief brief.md --format json`
- **TLS Certificate Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "TLS Certificate Manager" --brief brief.md --format json`
- **CDN and Cache Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "CDN and Cache Engineer" --brief brief.md --format json`
- **Edge Function Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Edge Function Developer" --brief brief.md --format json`
- **Reverse Proxy Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Reverse Proxy Engineer" --brief brief.md --format json`
- **Load Balancer Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Load Balancer Engineer" --brief brief.md --format json`
- **Static Site Hosting Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Static Site Hosting Specialist" --brief brief.md --format json`
- **Hosting Migration and Cutover Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Hosting Migration and Cutover Engineer" --brief brief.md --format json`

### 22. Containers, Infrastructure as Code, and Platform Engineering
- **Docker Application Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Docker Application Engineer" --brief brief.md --format json`
- **Container Image Security Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Container Image Security Engineer" --brief brief.md --format json`
- **Kubernetes Web Platform Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Kubernetes Web Platform Engineer" --brief brief.md --format json`
- **Terraform Infrastructure Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Terraform Infrastructure Developer" --brief brief.md --format json`
- **Pulumi Infrastructure Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Pulumi Infrastructure Developer" --brief brief.md --format json`
- **Ansible Automation Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Ansible Automation Engineer" --brief brief.md --format json`
- **Platform-as-a-Service Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Platform-as-a-Service Integrator" --brief brief.md --format json`
- **Internal Developer Platform Builder** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Internal Developer Platform Builder" --brief brief.md --format json`
- **Secrets and Configuration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Secrets and Configuration Engineer" --brief brief.md --format json`
- **Infrastructure Drift and Recovery Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Infrastructure Drift and Recovery Analyst" --brief brief.md --format json`

### 23. CI/CD, Testing, Deployment, and Release Engineering
- **Web CI Pipeline Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web CI Pipeline Developer" --brief brief.md --format json`
- **Continuous Deployment Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Continuous Deployment Engineer" --brief brief.md --format json`
- **Preview Environment Builder** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Preview Environment Builder" --brief brief.md --format json`
- **Blue-Green Deployment Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Blue-Green Deployment Engineer" --brief brief.md --format json`
- **Canary Release Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Canary Release Engineer" --brief brief.md --format json`
- **Feature Flag Systems Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Feature Flag Systems Developer" --brief brief.md --format json`
- **Automated Rollback Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Automated Rollback Engineer" --brief brief.md --format json`
- **Release Versioning Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Release Versioning Manager" --brief brief.md --format json`
- **Deployment Verification Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Deployment Verification Specialist" --brief brief.md --format json`
- **Clean-Environment Build Verifier** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Clean-Environment Build Verifier" --brief brief.md --format json`

### 24. Security, Privacy, and Application Defense
- **Web Security Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Security Architect" --project ./existing-site --format json`
- **OWASP Application Security Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "OWASP Application Security Reviewer" --project ./existing-site --format json`
- **Secure Coding Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Secure Coding Specialist" --project ./existing-site --format json`
- **Content Security Policy Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Content Security Policy Engineer" --project ./existing-site --format json`
- **Cross-Site Scripting Defense Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cross-Site Scripting Defense Engineer" --project ./existing-site --format json`
- **CSRF and Request Integrity Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "CSRF and Request Integrity Engineer" --project ./existing-site --format json`
- **SQL and Command Injection Defense Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SQL and Command Injection Defense Reviewer" --project ./existing-site --format json`
- **Secrets and Key Management Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Secrets and Key Management Engineer" --project ./existing-site --format json`
- **Privacy and Consent Systems Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Privacy and Consent Systems Developer" --project ./existing-site --format json`
- **Security Incident and Recovery Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Security Incident and Recovery Planner" --project ./existing-site --format json`

### 25. Performance, Core Web Vitals, and Optimization
- **Web Performance Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Performance Architect" --project ./existing-site --format json`
- **Core Web Vitals Optimizer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Core Web Vitals Optimizer" --project ./existing-site --format json`
- **JavaScript Bundle Optimizer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "JavaScript Bundle Optimizer" --project ./existing-site --format json`
- **Image and Media Optimization Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Image and Media Optimization Engineer" --project ./existing-site --format json`
- **Font Loading Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Font Loading Specialist" --project ./existing-site --format json`
- **Caching Strategy Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Caching Strategy Engineer" --project ./existing-site --format json`
- **Database Performance Optimizer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Database Performance Optimizer" --project ./existing-site --format json`
- **SSR and Hydration Performance Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SSR and Hydration Performance Engineer" --project ./existing-site --format json`
- **Mobile Network Performance Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Mobile Network Performance Tester" --project ./existing-site --format json`
- **Performance Budget and Regression Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Performance Budget and Regression Manager" --project ./existing-site --format json`

### 26. SEO, Discoverability, Analytics, and Experimentation
- **Technical SEO Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Technical SEO Engineer" --project ./existing-site --format json`
- **Structured Data and Rich Result Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Structured Data and Rich Result Developer" --project ./existing-site --format json`
- **Sitemap and Crawl Control Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Sitemap and Crawl Control Specialist" --project ./existing-site --format json`
- **International SEO Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "International SEO Developer" --project ./existing-site --format json`
- **Web Analytics Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Analytics Engineer" --project ./existing-site --format json`
- **Tag Management Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Tag Management Developer" --project ./existing-site --format json`
- **Consent-Aware Measurement Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Consent-Aware Measurement Specialist" --project ./existing-site --format json`
- **A/B Testing Platform Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "A/B Testing Platform Developer" --project ./existing-site --format json`
- **Conversion Funnel Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Conversion Funnel Analyst" --project ./existing-site --format json`
- **Search and Analytics Data Quality Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Search and Analytics Data Quality Reviewer" --project ./existing-site --format json`

### 27. Progressive Web Apps, Offline, and Installability
- **Progressive Web App Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Progressive Web App Architect" --project ./existing-site --format json`
- **Service Worker Caching Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Service Worker Caching Engineer" --project ./existing-site --format json`
- **Offline-First Data Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Offline-First Data Developer" --project ./existing-site --format json`
- **Web App Manifest Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web App Manifest Specialist" --project ./existing-site --format json`
- **Push Notification Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Push Notification Developer" --project ./existing-site --format json`
- **Background Sync Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Background Sync Engineer" --project ./existing-site --format json`
- **Install Experience Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Install Experience Designer" --project ./existing-site --format json`
- **Offline Conflict Resolution Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Offline Conflict Resolution Engineer" --project ./existing-site --format json`
- **PWA Update and Version Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "PWA Update and Version Manager" --project ./existing-site --format json`
- **PWA Compatibility Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "PWA Compatibility Tester" --project ./existing-site --format json`

### 28. Electron, Cordova, Capacitor, and Hybrid Applications
- **Electron Desktop App Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Electron Desktop App Developer" --brief brief.md --format json`
- **Electron Security and Packaging Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Electron Security and Packaging Engineer" --brief brief.md --format json`
- **Apache Cordova Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Apache Cordova Developer" --brief brief.md --format json`
- **Capacitor Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Capacitor Application Developer" --brief brief.md --format json`
- **Hybrid WebView Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Hybrid WebView Engineer" --brief brief.md --format json`
- **Native Bridge and Plugin Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Native Bridge and Plugin Developer" --brief brief.md --format json`
- **Desktop Auto-Update Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Desktop Auto-Update Engineer" --brief brief.md --format json`
- **Cross-Platform Installer Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cross-Platform Installer Developer" --brief brief.md --format json`
- **Hybrid App Performance Optimizer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Hybrid App Performance Optimizer" --brief brief.md --format json`
- **Web-to-Desktop and Mobile Migration Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web-to-Desktop and Mobile Migration Specialist" --brief brief.md --format json`

### 29. Media, Streaming, WebRTC, and Interactive Content
- **Web Audio Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Audio Developer" --project ./existing-site --format json`
- **HTML Media Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "HTML Media Application Developer" --project ./existing-site --format json`
- **Adaptive Video Streaming Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Adaptive Video Streaming Engineer" --project ./existing-site --format json`
- **WebRTC Communication Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "WebRTC Communication Developer" --project ./existing-site --format json`
- **Live Streaming Platform Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Live Streaming Platform Engineer" --project ./existing-site --format json`
- **Media Recording Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Media Recording Developer" --project ./existing-site --format json`
- **Image Processing Web Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Image Processing Web Developer" --project ./existing-site --format json`
- **Canvas and Creative Coding Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Canvas and Creative Coding Developer" --project ./existing-site --format json`
- **Three-Dimensional Web Experience Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Three-Dimensional Web Experience Developer" --project ./existing-site --format json`
- **Media Accessibility and Playback Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Media Accessibility and Playback Reviewer" --project ./existing-site --format json`

### 30. Portals, SaaS, Dashboards, and Business Applications
- **SaaS Application Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SaaS Application Architect" --brief brief.md --format json`
- **Admin Dashboard Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Admin Dashboard Developer" --brief brief.md --format json`
- **Customer Portal Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Customer Portal Developer" --brief brief.md --format json`
- **Multi-Tenant Systems Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Multi-Tenant Systems Engineer" --brief brief.md --format json`
- **Subscription and Billing Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Subscription and Billing Developer" --brief brief.md --format json`
- **Workflow Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Workflow Application Developer" --brief brief.md --format json`
- **Reporting and Data Dashboard Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Reporting and Data Dashboard Developer" --brief brief.md --format json`
- **Document and File Management Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Document and File Management Developer" --brief brief.md --format json`
- **Role-Based Business Application Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Role-Based Business Application Developer" --brief brief.md --format json`
- **Enterprise Integration Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Enterprise Integration Specialist" --brief brief.md --format json`

### 31. Specialized Website Types and Digital Experiences
- **Portfolio Website Designer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Portfolio Website Designer" --brief brief.md --format json`
- **Corporate Website Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Corporate Website Developer" --brief brief.md --format json`
- **Educational Platform Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Educational Platform Developer" --brief brief.md --format json`
- **Community and Forum Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Community and Forum Developer" --brief brief.md --format json`
- **Marketplace Platform Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Marketplace Platform Developer" --brief brief.md --format json`
- **Booking and Reservation Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Booking and Reservation Developer" --brief brief.md --format json`
- **Real Estate Website Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Real Estate Website Developer" --brief brief.md --format json`
- **Healthcare Information Website Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Healthcare Information Website Developer" --brief brief.md --format json`
- **Government and Public Service Website Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Government and Public Service Website Developer" --brief brief.md --format json`
- **Event and Ticketing Website Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Event and Ticketing Website Developer" --brief brief.md --format json`

### 32. Internationalization, Localization, and Global Delivery
- **Internationalization Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Internationalization Architect" --project ./existing-site --format json`
- **Localization Pipeline Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Localization Pipeline Developer" --project ./existing-site --format json`
- **Multilingual Routing Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Multilingual Routing Developer" --project ./existing-site --format json`
- **Right-to-Left Layout Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Right-to-Left Layout Engineer" --project ./existing-site --format json`
- **Locale Formatting Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Locale Formatting Specialist" --project ./existing-site --format json`
- **Translation Management Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Translation Management Integrator" --project ./existing-site --format json`
- **Regional Content and Compliance Router** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Regional Content and Compliance Router" --project ./existing-site --format json`
- **Global CDN and Region Strategy Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Global CDN and Region Strategy Engineer" --project ./existing-site --format json`
- **Localization QA Reviewer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Localization QA Reviewer" --project ./existing-site --format json`
- **Language Fallback and Content Parity Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Language Fallback and Content Parity Tester" --project ./existing-site --format json`

### 33. Quality Assurance, Browser Testing, and Reliability
- **Web Test Strategy Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Test Strategy Architect" --project ./existing-site --format json`
- **Unit and Component Test Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Unit and Component Test Developer" --project ./existing-site --format json`
- **End-to-End Test Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "End-to-End Test Engineer" --project ./existing-site --format json`
- **Visual Regression Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Visual Regression Tester" --project ./existing-site --format json`
- **Cross-Browser Compatibility Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cross-Browser Compatibility Tester" --project ./existing-site --format json`
- **Mobile Device Matrix Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Mobile Device Matrix Tester" --project ./existing-site --format json`
- **API Contract Test Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "API Contract Test Developer" --project ./existing-site --format json`
- **Load and Stress Test Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Load and Stress Test Engineer" --project ./existing-site --format json`
- **Chaos and Failure Injection Tester** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Chaos and Failure Injection Tester" --project ./existing-site --format json`
- **Bug Triage and Root Cause Analyst** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Bug Triage and Root Cause Analyst" --project ./existing-site --format json`

### 34. Observability, Operations, Cost, and Disaster Recovery
- **Application Observability Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Application Observability Engineer" --brief brief.md --format json`
- **Log and Trace Systems Developer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Log and Trace Systems Developer" --brief brief.md --format json`
- **Uptime and Synthetic Monitoring Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Uptime and Synthetic Monitoring Engineer" --brief brief.md --format json`
- **Error Tracking Integrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Error Tracking Integrator" --brief brief.md --format json`
- **SRE for Web Platforms** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "SRE for Web Platforms" --brief brief.md --format json`
- **Cloud Cost Optimization Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Cloud Cost Optimization Engineer" --brief brief.md --format json`
- **Capacity and Scaling Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Capacity and Scaling Planner" --brief brief.md --format json`
- **Backup and Restore Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Backup and Restore Engineer" --brief brief.md --format json`
- **Disaster Recovery Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Disaster Recovery Architect" --brief brief.md --format json`
- **Production Incident Commander** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Production Incident Commander" --brief brief.md --format json`

### 35. Legacy Modernization, Migration, and Preservation
- **Legacy HTML and CSS Modernizer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Legacy HTML and CSS Modernizer" --brief brief.md --format json`
- **Legacy JavaScript Refactoring Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Legacy JavaScript Refactoring Engineer" --brief brief.md --format json`
- **jQuery Migration Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "jQuery Migration Specialist" --brief brief.md --format json`
- **PHP Legacy Application Maintainer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "PHP Legacy Application Maintainer" --brief brief.md --format json`
- **Framework Upgrade Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Framework Upgrade Specialist" --brief brief.md --format json`
- **Database Modernization Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Database Modernization Engineer" --brief brief.md --format json`
- **Monolith Decomposition Architect** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Monolith Decomposition Architect" --brief brief.md --format json`
- **Hosting and Cloud Migration Engineer** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Hosting and Cloud Migration Engineer" --brief brief.md --format json`
- **URL and SEO Migration Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "URL and SEO Migration Manager" --brief brief.md --format json`
- **Web Archive and Preservation Specialist** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Archive and Preservation Specialist" --brief brief.md --format json`

### 36. Autonomous Web Creation and Agent Orchestration
- **Prompt-to-Website Production Planner** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Prompt-to-Website Production Planner" --brief brief.md --format json`
- **Multimodal Web Brief Interpreter** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Multimodal Web Brief Interpreter" --brief brief.md --format json`
- **Image-to-Web Layout Builder** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Image-to-Web Layout Builder" --brief brief.md --format json`
- **Autonomous Full-Stack Prototype Builder** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Autonomous Full-Stack Prototype Builder" --brief brief.md --format json`
- **Autonomous Repository Repair Agent** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Autonomous Repository Repair Agent" --brief brief.md --format json`
- **Web Stack and Hosting Router** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Web Stack and Hosting Router" --brief brief.md --format json`
- **Asset and Dependency Orchestrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Asset and Dependency Orchestrator" --brief brief.md --format json`
- **Adaptive Build Test and Deploy Manager** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Adaptive Build Test and Deploy Manager" --brief brief.md --format json`
- **Human Review and Approval Coordinator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "Human Review and Approval Coordinator" --brief brief.md --format json`
- **End-to-End Web Production Orchestrator** — Example: `python3 ultimate-web-maker/tools-webmaker.py run --selection "End-to-End Web Production Orchestrator" --brief brief.md --format json`
