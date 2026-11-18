# Google Sheets + Apps Script setup

This is the shared record every device syncs into. About 12 minutes, once.

## What you need

* A Google account (a normal free one is fine).
* The workbook in `excel/{{ARTIFACT_SLUG}}-Google-Sheet-Template.xlsx`.
* The three files in `google-apps-script/`: `Code.gs`, `Config.gs`, `Installer.gs`.

## Step 1 — upload the workbook

1. Go to <https://drive.google.com> → **New → File upload** → choose the `.xlsx`.
2. Right-click it in Drive → **Open with → Google Sheets**.
3. **File → Save as Google Sheets** (this gives you a real Sheet, not an import).

## Step 2 — open Apps Script

In the Sheet: **Extensions → Apps Script**. Delete the placeholder code, then paste
`Code.gs` as `Code.gs`, add `Config.gs` and `Installer.gs` with the **+** button.

## Step 3 — set your access token

In `Config.gs`:

```js
var CONFIG = {
  ACCESS_TOKEN: 'paste-a-long-random-string-here',   // <-- change this
  SPREADSHEET_ID: '',                                // blank = the sheet this script is bound to
  TIME_ZONE: '{{TIME_ZONE}}',
  OWNER_USERNAME: '{{OWNER_USER}}',
  OWNER_PASSWORD: '{{OWNER_PASSWORD}}'
};
```

Generate a strong token on your machine:

```bash
bash tools/generate-token.sh           # or: openssl rand -hex 32
```

The generated project ships with `{{DEFAULT_TOKEN}}`. The backend **rejects**
anything beginning with `CHANGE-ME-`, so the app cannot talk to a public
deployment until you edit this line. That is deliberate.

## Step 4 — run the installer

1. In the editor pick the function **installKit** and press **Run**.
2. Approve the permission screen (*Advanced → Go to project → Allow*).
3. It creates the 11 sheets, writes your branches, roles, accounts, products and
   settings from the specification, and protects the password columns.

Expected log:

```
{{APP_TITLE}} installed
Owner login: {{OWNER_USER}} / ... (change it after first sign-in)
```

## Step 5 — deploy as a Web App

1. **Deploy → New deployment → Web app**.
2. Description: anything. **Execute as: Me**. **Who has access: Anyone**.
3. Copy the URL that ends in `/exec`.

> "Anyone" is required: the app is a static file and has no Google session. The
> access token is what protects the endpoint. Keep the token secret.

## Step 6 — connect the app

1. Open the app → on the sign-in screen press **Set up**.
2. Paste the `/exec` URL and the token → **Save & test**. You should see
   `OK · MOCK`/`OK · <sheet name>` and the server time.
3. Sign in, open **Settings → Pull menu & settings**.

## Step 7 — verify

| Check | Expected |
| --- | --- |
| Make a sale | A row appears in `Orders` plus its lines in `OrderItems`. |
| Kitchen status change | The same `Orders` row updates and `Revision` increases. |
| Sync page | The record turns from **Pending** to **Sent**. |
| `SyncLog` | One row per send/login, with device and result. |
| Wrong token | `{"ok":false,"error":"Access token is not valid."}` |

## What the sheets are

| Sheet | Purpose |
| --- | --- |
| `Accounts` | Sign-in rows. Salt + SHA-256 hash only, never plain text. |
| `Roles` | Permissions per role; the app reads this on every pull. |
| `Branches` | Branch ID, name and the queue-card range. |
| `Products` | Your catalogue, price, category, `VolumeJSON`. |
| `Orders` | One row per order, written by the app. |
| `OrderItems` | One row per line item. |
| `KitchenQueue` | Every status change with time and staff. |
| `NonRevenueLogs` | Non-sales counts. Excluded from revenue totals. |
| `Settings` | App behaviour, including the feature flags. |
| `Reports` | Nightly per-branch totals with `PaymentBreakdownJSON`. |
| `SyncLog` | Trace of every send, login and void. |

## Passwords

Never type a password into the sheet. The app hashes it in the browser:

```
sha256$ + SHA256( salt + "|" + password )
```

`PasswordSalt` + `PasswordHash` + `PinHash` are all the sheet stores.

## Re-deploying after a code change

**Deploy → Manage deployments → edit the existing one → Version: New version.**
The `/exec` URL stays the same, so the app keeps working.
