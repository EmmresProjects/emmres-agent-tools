# Project handoff — {{PROJECT}}

Prepared by **{{OWNER}}** on {{DATE}} using the powerful-extractor-compressor
`project-handoff` recipe. This file is generated automatically; edit the copy
inside the package, not the template.

## What is in this package

| Item | Value |
| --- | --- |
| Files | {{FILE_COUNT}} |
| Uncompressed size | {{TOTAL_SIZE}} |
| Archive contents | `handoff/` root folder containing the project files and this `HANDOFF.md` |

Every file's size and hash is listed in `pec-package-manifest.json`, which is
published next to this archive (not inside it). Verify the extracted package with:

```sh
python3 tools-pec.py verify --manifest pec-package-manifest.json --dir <extracted-folder>
```

## How to pick this up

1. Extract the archive into a working directory.
2. Read `pec-package-index.md` for the full file list.
3. Confirm the manifest audit passes before relying on the content.
4. Install or restore dependencies from the files that were included
   (lockfiles, requirement lists, configuration examples).
5. Run the project's own documented checks (tests, linters, builds).

## What was excluded on purpose

- Generated and build output (`dist/`, `build/`, `node_modules/`, caches)
- Logs, temporary files and editor swap files
- Secrets and environment files (`.env`, `*.pem`, `*.key`, `id_rsa*`, `*.p12`)
- Version-control internals (`.git/`)

If something you need is missing, check those categories first, then ask the
sender to rebuild the package with a wider `--set include=` pattern.

## Open items for the receiving team

- [ ] Confirm ownership and on-call contact
- [ ] Confirm credentials and access were transferred through an approved channel
- [ ] Confirm the manifest audit passed on the receiving side
- [ ] Record the extraction date and location in your own handoff log

## Provenance

The package manifest records the source fingerprint, job id, recipe options and
per-file SHA-256 values so this handoff can be reproduced and audited later.
