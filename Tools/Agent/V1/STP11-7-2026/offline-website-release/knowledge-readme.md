# Offline website release — Skill Tool component

## Scope and important limitation

This is **not a complete implementation of the PDF catalog** and is not a complete Copilot skill. It is an offline, standard-library toolkit with five implemented operations described below. The supplied PDF contains 30 categories and 1,238 concepts; their names and descriptions are preserved as searchable source material in `offline-website-release/asset-catalog.json`. Catalog entries are **not** runnable implementations and searching them does not implement them. In particular, this tool does not implement most framework packs, application backends, authentication, commerce, browser automation, WCAG 2.2 AA conformance testing, Lighthouse/Core Web Vitals, deployment, hosting, DNS, TLS, monitoring, backup/restore, or the many other individual catalog concepts. No claim of production readiness follows from its reports. A real release still needs independent implementation and evidence for each applicable concept.

Implemented capabilities:

1. **catalog**: filter the complete supplied catalog by category and/or text; return original names and descriptions with category/item numbers.
2. **scaffold**: create a new minimal responsive static HTML/CSS starting page from a user-supplied JSON document; escape content and perform a post-generation static check. It has no framework or backend.
3. **audit**: statically check locally stored HTML for selected metadata, landmarks, image attributes, form-label heuristics, local references, and external resources.
4. **verify-artifacts**: verify explicitly listed local files against caller-supplied SHA-256 hashes; report mismatches and missing files.
5. **readiness**: summarize caller-asserted evidence for a conservative release gate checklist, including explicit release approval. It does not independently verify evidence or publish anything.

The catalog covers strategy; local runtimes; offline package caches; framework scaffolding; browser foundations; bundling; browser automation; responsive design; backend/API; databases; authentication; forms/uploads/email; commerce; search/CMS; media; privacy; application security; supply-chain security; accessibility; performance; SEO; localization; progressive web apps; testing; observability; hosting/DNS/TLS; credentials; recovery; CI/CD; and production operations. Use `catalog` for the exact names, descriptions and category boundaries rather than treating this summary as implementation coverage.

## Files and runtime

Copy the `offline-website-release` folder unchanged directly beside your existing root `SKILL.md`. The component itself has no `SKILL.md`. All commands below use **POSIX sh**, starting at the future skill root. On Windows use an equivalent shell and Python command. The primary entry point is `offline-website-release/tools-website.py`; `offline-website-release/tools-selftest.py` is an optional offline end-to-end test driver which runs the primary entry point in isolated temporary directories. `offline-website-release/asset-catalog.json` supplies the source reference consumed by `catalog`; do not remove it.

Python 3.13.16 was tested. The code uses standard-library APIs available in Python 3.9+, but other Python versions were not tested here. No pip installation, network service, credentials, or third-party dependency is needed. The source PDF was parsed to build the catalog asset; the PDF and PDF parsing library are not runtime dependencies.

```sh
python3 offline-website-release/tools-website.py --help
python3 offline-website-release/tools-website.py capabilities
python3 offline-website-release/tools-selftest.py
```

Each subcommand has `--help`. Outputs are UTF-8 JSON to stdout by default; normal help is plain text. Add `--output report.json` to any operation to write a JSON report instead. Its parent must exist, it must be outside the tool folder and input site/artifact directory, and must not be an input file. An existing report is never replaced unless the caller explicitly approves with `--replace`. Output uses a temporary file in the destination directory, then an atomic replace or exclusive link. Do not use the report itself as an input. Reports may contain paths, URLs and evidence text: store them according to your data classification policy.

Exit codes: 0 for a successfully completed operation with passing applicable checks; 1 for audit errors, artifact mismatches/missing files, or readiness gates that are not all ready; 2 for invalid input, filesystem or other handled errors; 130 for interruption. Errors are JSON objects on stderr, with `error.type` and `error.message`. Warnings alone do not cause exit 1. Shell errors such as a missing Python executable occur before the program starts and are not formatted by the tool.

## Inputs, operations and examples

All CLI paths are caller-selected. Example input/report/site files below are created in the skill root, *outside* the component folder; adjust names as needed. The component has no provider/model configuration because it invokes no models or services.

### Catalog

```sh
python3 offline-website-release/tools-website.py catalog --category 19 --query keyboard --output catalog-report.json
```

`--category` is a category number as a string or a case-insensitive category-name substring. `--query` is a case-insensitive substring of a skill name or description. Both filters combine. Omit both to return all 1,238 records. A result has `operation`, `count`, `items`, and a warning note. Each item has `category_number`, `category`, `number`, `name`, and `description`. Category numbers match the PDF; individual item numbers restart in each category. Descriptions were text-extracted and normalized for whitespace, including line wrapping.

### Scaffold

Create an input JSON file and then create a **new**, nonexistent output directory:

```sh
cat > site-input.json <<'JSON'
{"title":"Community Garden", "description":"A neighborhood garden guide", "heading":"Grow together", "body":"Find out how to take part.", "language":"en"}
JSON
python3 offline-website-release/tools-website.py scaffold --input site-input.json --site local-site --output scaffold-report.json
python3 offline-website-release/tools-website.py audit --site local-site --output audit-report.json
```

The input must be an object with nonempty `title`, `description`, `heading`, `body` strings (up to 10,000 characters each). Optional `language` is a simple BCP-47-shaped language tag, default `en`. Strings are HTML-escaped, including quotes; body is plain text, not HTML/Markdown. Output consists of `index.html` and `styles.css`, plus a JSON result with created path, filenames, and static audit counts. The output parent must already exist and the site directory must not exist. The tool builds and audits in a sibling temporary directory, reserves a new destination, and removes partially populated output if copying fails. It does not overwrite sites. Browse the generated page locally to review it; this tool does not launch a browser.

### Audit

```sh
python3 offline-website-release/tools-website.py audit --site local-site
```

Walks up to 1,000 files, ignores symlinked files and directories, and examines UTF-8 `.html`/`.htm` files (maximum 2 MiB each). At least one HTML file is required. Checks include doctype, language, nonempty title, viewport and description metadata, h1/main presence, image alt, `_blank` link `noopener`, labels on form controls, local link/resource existence, path escape, JavaScript URLs, and absolute external resource references. Findings have `path`, `code`, `severity` and `message`; summary contains error and warning counts. Root-relative links resolve within the specified directory; directory links map to `index.html`. Fragment links are not checked. External anchors are not treated as resources. Relative paths pointing outside the site are errors; external resources are listed as warnings but are not fetched.

These checks are intentionally incomplete. A wrapping `<label>` can trigger a label-review warning; the static checker cannot prove accessible names, quality of alt text, visual layout, code execution, contrast, keyboard behavior, framework-rendered routes, HTTP headers, security posture, SEO correctness, or offline operation. CSS imports and references inside JavaScript are not checked. It cannot establish compliance with any accessibility standard. Human and browser-based tests remain mandatory.

### Verify offline artifacts

Create your manifest from trusted, independently acquired reference digests; do not compute a digest from an untrusted file and treat that as proof of authenticity. An example manifest is:

```sh
mkdir artifact-demo
printf 'hello' > artifact-demo/greeting.txt
cat > artifact-manifest.json <<'JSON'
{"artifacts":[{"path":"greeting.txt","sha256":"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"}]}
JSON
python3 offline-website-release/tools-website.py verify-artifacts --root artifact-demo --manifest artifact-manifest.json
```

The example digest corresponds to the literal demonstration file, not a production trust source. The object must contain an `artifacts` array of 1–1,000 objects, each with a unique forward-slash relative `path` (no absolute paths, colon, backslash, empty segments, `.` or `..`) and a 64-hex-character `sha256`. Symlink components are rejected. Each file is at most 100 MiB. Results have `path`, `status` (`verified`, `mismatch`, or `missing`), and for present files `actual_sha256` and `bytes`; `all_verified` is true only if all match. Matching a digest does not verify provenance, license, compatibility, dependency closure, security, or ability to restore with networking blocked. Test those separately.

### Release evidence

```sh
cat > release-evidence.json <<'JSON'
{"gates":{"offline_restore":{"status":"unknown"},"release_approval":{"status":"unknown"}}}
JSON
python3 offline-website-release/tools-website.py readiness --input release-evidence.json
```

Input is an object containing a `gates` object with zero or more of these exact keys: `offline_restore`, `build_tests`, `browser_journeys`, `accessibility`, `performance`, `security_privacy`, `seo`, `backup_restore`, `rollback`, `dns_tls_hosting`, `monitoring`, `release_approval`. Each value is an object with `status` (`pass`, `fail`, `unknown`, `not_applicable`), and optional string `owner` and `evidence`. Omitted gates become `unknown`. A `pass` needs nonempty evidence; `not_applicable` needs a nonempty justification in `evidence`. Every gate must be `pass` or justified `not_applicable`, and `release_approval` specifically must be `pass`, for `ready` to be true. The tool validates the data shape and summarizes the assertions, but never checks whether assertions, evidence, owners, approval, or a site are genuine. A true result is an evidence checklist state, not authorization, certification, or a production deployment. Unknown gate names and malformed statuses fail validation.

## Safety, limits, and recovery

The tool performs no network calls, shell command execution, provider calls, publishing, payment, privileged operations, or credential handling. It requires no approval for read-only checks or creating a new site/report. The explicit `--replace` flag is approval to replace an existing report; it never authorizes site replacement, deployment, or other operations. Supply an external, independently authorized process for any sensitive action. Do not place credentials in input files or evidence.

JSON inputs are limited to 2 MiB. The catalog is local and bounded by this same limit. Audit is limited to 1,000 files, with 2 MiB per HTML page; artifact verification is limited to 1,000 entries at 100 MiB each. These caps do not impose a total runtime or a filesystem I/O timeout. There are no network retries or background processes. Cancel with Ctrl-C; generated temporary directories/files are cleaned up on ordinary exceptions and interruption, but a hard kill or host failure may leave a `.website-build-*` directory, `.website-output-*` file, or incomplete reserved output directory. Inspect and remove leftovers manually only after confirming they are not active or needed. Check results and rerun with a fresh output directory. There is no protection against a malicious concurrent actor changing input paths during checking; use a trusted, stable local workspace.

## Validation actually completed

The bundled regression driver exercises primary help, capability listing, catalog count and filtering, site generation and escaping, static audit (passing and failing), changed input, existing-directory rejection followed by successful generation at a new destination, artifact success/mismatch/missing/traversal rejection, evidence pass/unknown/invalid states, missing/malformed input, JSON output and report-replacement approval. It inspects generated HTML and parses JSON reports. These are isolated local tests, not browser, framework, internet, production or platform tests. Run the test driver after copying or modifying the component. Further clean-root and ZIP extraction checks were performed for the delivered archive; they do not verify a hosted catalog or any external platform.

## Troubleshooting and maintenance

For exit 2, read the JSON error on stderr and check input type, size, spelling, parent directories and permissions. For exit 1, inspect findings or gate statuses, fix the input or site, and rerun. Use a fresh site path if scaffold refuses an existing directory; do not delete existing content without separate approval. A mismatch may indicate an altered artifact or an incorrect trusted digest: independently establish which before replacing either. For broken-reference findings, remember routes that need a server or SPA router cannot be inferred from static files. For external-resource findings, acquire and license required resources separately and test in a truly network-blocked environment.

To extend this component, add bounded, independently tested operations to the primary tool or a functionally justified `tools-` module, add any necessary `asset-` resources, and update this single knowledge file. Do not imply that a catalog description has become implemented without a real command and validation. Keep secrets and external integrations out of the package, and document any new integration's exact prerequisites and verified limitations.
