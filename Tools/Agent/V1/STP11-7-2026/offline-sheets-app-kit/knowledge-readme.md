# offline-sheets-app-kit — knowledge

The reusable capability behind this kit: **turn one declarative JSON
specification into a complete, offline-first, Google-Sheets-backed business
application** — web app, Apps Script backend, workbook, Android build, Windows
build and the documentation that makes it usable — without touching the app
source for each new business.

It was reverse-engineered from a delivered chicken-restaurant POS. Nothing about
that restaurant remains: products, payment methods, non-revenue reasons, branch
names, roles, colours, icons, wording and build ids all come from the
specification. Two worked examples ship with it (a café and a sari-sari store)
because a restaurant-only generator would be a template, not a capability.

---

## 1. What the generated project always contains

```
<app-slug>/
├── app/                      the offline-first web app
│   ├── index.html            shell, loads css + js in order
│   ├── <Artifact>.html       single self-contained file (generated)
│   ├── css/app.css           dark theme, print rules, touch targets
│   ├── img/                  icon.svg + PNGs generated from brand colours
│   └── js/                   util · db · seed · api · pos · kitchen · orders
│                             reports · nonrev · admin · syncpage · settings · app
├── google-apps-script/       Code.gs · Config.gs · Installer.gs (SEED embedded)
├── excel/                    <Artifact>-Google-Sheet-Template.xlsx (12 tabs)
├── cordova/                  Android project, icons, splashes, one-command build
├── electron/                 Windows project, icons, licence, one-command build
├── docs/                     seven guides, written for this exact app
├── tools/                    mock server, builders, three tests
├── dist/                     build output lands here
├── start-mock-server.sh
└── README.md
```

~51 files, ~1.4 MB, zero build steps needed to open it.

## 2. Invariants (true for every generated app)

These are the behaviours that made the original app worth reusing. The kit keeps
them in every output:

1. **Offline first.** Records are written to the device (IndexedDB, localStorage
   fallback) before anything else. The UI never waits on the network.
2. **Four sync states** — Pending, Sent, Needs Review, plus offline — and nothing
   is ever silently dropped. A rejected record stays visible with its reason.
3. **`RecordID` is identity.** Re-sending updates the sheet row instead of
   appending a duplicate; the backend reports `ok | duplicate | review | error`
   per record.
4. **Every record carries** Record ID, Branch ID, Account ID, business date and
   timestamp — in the app and in the sheet.
5. **Money is never trusted from the client alone**: totals, discounts, change and
   volume counts are recomputed and stored per line and per order.
6. **No plaintext passwords.** `sha256$` + SHA-256(salt + "|" + password), hashed
   once by the generator so the app seed, the workbook and the Apps Script
   installer all agree on the first sign-in. Login attempts, sends and voids are
   logged.
7. **Roles gate the UI and the actions**: permissions come from the spec, the
   sidebar hides what a role cannot use, and a cashier can only void their own
   order inside `VoidWindowMinutes`.
8. **Non-revenue is counted, never sold.** Stock-loss lines are excluded from all
   revenue totals while still appearing in volume counts.
9. **Modules can be switched off without code changes** (kitchen, queue cards,
   discounts, multi-branch, unpaid orders, product volume). Off means the nav
   entry, the page and the button disappear and `App.go()` refuses to navigate
   there.
10. **Everything is data-driven at runtime** from the `Settings` sheet, so an owner
    can change currency, receipt footer, timings and feature flags after
    deployment — and the change survives a pull.

### Flat templates, encoded destinations

The kit holds **no subfolders**: every template is one prefixed flat file whose
destination path is encoded in its name.

```
asset-tpl-app__js__pos.js        ->  <project>/app/js/pos.js
asset-tpl-cordova__config.xml    ->  <project>/cordova/config.xml
asset-tpl-electron__main.js      ->  <project>/electron/main.js
```

`node tools-template-index.js --list` prints the whole map;
`--placeholders` lists the 29 `{{TOKEN}}` names the generator must supply (an
unknown token is a hard error, never an empty string).

## 3. The specification is the single source of truth

`tools-generate-kit.py` validates it, normalises defaults, computes password
hashes, then renders one token map into every template. Fields:

| Group | Drives |
| --- | --- |
| `app` | title, business name, slug (storage prefix), colours → CSS vars, icon and splash images, initials, Android/iOS app id, Electron app id, version, orientation, keystore alias, timezone |
| `categories` | POS tabs and product grouping (labels, not ids, are shown) |
| `products` | POS grid, price, `volumeLabel` badge, combo tag, `VolumeJSON` counts |
| `paymentMethods` | checkout buttons in order, first = default, icon per name, report columns, CSV headers, sheet validation |
| `nonRevenueTypes` | non-revenue tile set, entry dialog, sidebar label, report badges, sheet validation |
| `branches` | branch rows, queue range, selector, reports comparison columns |
| `roles` | permissions, sidebar visibility, per-action gating, sheet `Roles` tab |
| `accounts` | sign-in rows, seed hashes, workbook `Accounts` tab, test credentials |
| `features` | which modules exist at all (also written to `Settings` as `Feature*`) |
| `queue` | queue card range |
| `settings` | extra `Settings` rows (currency, timings, footer, page hints) |
| `token` | the sync token placeholder; empty ⇒ random `CHANGE-ME-…` the backend refuses |

Adding a new business is writing one JSON file — never editing the templates.

## 4. Pipeline

```
spec.json
   │  validate_spec()      → JSON-path errors, hard limits, warnings
   │  normalise()          → defaults for every optional field
   │  annotate_accounts()  → salt + SHA-256 hashes computed once
   │  build_seed()         → one compact SEED object (arrays) + tokens
   ├─ render asset-tpl-* (29 flat templates)        → app/, cordova/, electron/, gs/
   ├─ tools-build-excel-template.py --spec …        → excel/*.xlsx (12 tabs)
   ├─ tools-make-icons.py --spec …                  → icons + splashes (29 PNG/SVG)
   ├─ tools-build-single-file.py --app app --out …  → app/<Artifact>.html
   └─ node --check every .js/.gs                    → syntax gate
```

Sub-tools are plain CLIs with their own `--help`, so any one of them can be used
on its own against an existing project.

### Why the seed is one object in three places

The browser, the workbook and the Apps Script installer must agree on password
hashes and on row shape. The generator emits a single `SEED` (arrays, not
objects: compact and identical everywhere) into `app/js/seed.js` and into
`Installer.gs`. Mappers on both sides (`App.mapRole/mapProduct/mapAccount/
mapBranch`) accept either the array shape or the sheet's PascalCase shape, so a
seeded row and a pulled row behave identically. **This is the part most likely to
break if the kit is extended — change all four pieces together.**

## 5. Adding a capability to the kit

1. Put the behaviour in the relevant `asset-tpl-app__js__*.js`, driven by
   `App.setting()` / `App.feature()` — never by a literal.
2. If it needs a spec field, add it to `asset-spec-schema.json`, to `normalise()`
   (defaults) and to `validate_spec()` (rules).
3. If it needs a new seed row or column, update `build_seed()` plus the sheet
   headers in `tools-build-excel-template.py` and the seed reader in
   `Installer.gs`/`asset-mock-server.js`.
4. If it is a module a shop may not want, register it in `App.FEATURES` with its
   setting key, nav ids and element selectors, then add a `Feature*` default in
   `normalise()`.
5. Re-run both example specs and the three generated tests before shipping.

## 6. Deliberate limits

* **Server-side Apps Script is not executed here.** No Google account is used and
  no deployment is created. `Code.gs`/`Installer.gs` are syntax-checked and the
  embedded `SEED` is parsed and hash-verified; the contract they implement is
  exercised through `tools/mock-server.js`, which mirrors their response shapes.
* **Android and Windows installers are not built here.** The projects, icons,
  scripts and commands are complete; building needs the JDK/Android SDK and a
  Windows toolchain. The commands were kept identical to the proven originals.
* **One business per project.** Multi-tenant hosting is out of scope: each app has
  its own sheet and token.
* **No printing SDK.** Receipts use a print stylesheet.
* **Currency is a symbol + string formatting.** Multi-currency and tax engines are
  out of scope; add them as a spec field if a business needs them.

## 7. Where the kit has been proven

| Check | Result |
| --- | --- |
| Café spec (20 products, 2 branches, 4 roles, 5 accounts, 3 payment methods, 3 non-revenue types) | generated, all 4 build steps pass |
| Sari-sari spec (10 products, 1 branch, 2 roles, kitchen/queue/discount/unpaid/multi-branch off) | generated, all 4 build steps pass |
| Spec-conformance browser test (café) | 11/11 |
| Spec-conformance browser test (sari-sari) | 9/9 |
| Sync test against the mock backend (café) | 10/10 |
| Sync test against the mock backend (sari-sari) | 10/10 |
| Edge test — restricted role, qty 50, discount, unpaid, exports (café) | 9/9 |
| Edge test (sari-sari) | 7/7 |
| Single-file HTML, both apps | login + POS render, no console errors |
| Kit folder renamed inside a foreign root with a dummy SKILL.md, relative paths only | generation succeeds, output renders in a browser with no console errors |
| Validation, approval gate, missing-spec, bad-JSON paths | structured errors, documented exit codes |
