# Troubleshooting

## The app will not open

| Symptom | Cause | Fix |
| --- | --- | --- |
| Blank page from `app/index.html` | Browser blocked local scripts | Use `app/{{ARTIFACT_SLUG}}.html` (single file) — it works everywhere. |
| `DB is not defined` in the console | A file failed to load | Check the `js/` files were not renamed; the load order is in `index.html`. |
| "Preview mode" toast, data disappears | The page is inside a sandboxed frame | Normal. Open the single-file HTML directly for real storage. |

## Sign-in

| Symptom | Fix |
| --- | --- |
| "Wrong username or password" | Usernames are case-insensitive, passwords are not. Check `accounts` in the spec. |
| Sign-in works offline but not online | The sheet's `Accounts` row was hashed with a different salt. Re-run `installKit` on an empty `Accounts` sheet, or use **Settings → Pull menu & settings** to take the sheet as truth. |
| Locked out entirely | Edit `Accounts` in the sheet: set `PasswordHash` to `sha256$` + SHA256(salt+"|"+new password). Or delete the sheet rows and re-run `installKit`. |

## Syncing

| Status | Meaning | Action |
| --- | --- | --- |
| Pending | Saved on the device, not sent yet | Let it run, or press **Sync now**. |
| Sent | Confirmed by the server | Nothing. |
| Needs Review | Server rejected it (validation, duplicate, unknown product) | Open Sync, read the reason, fix and retry. |

| Symptom | Fix |
| --- | --- |
| `Access token is not valid.` | The token in the app and in `Config.gs` differ, or you left `CHANGE-ME-…`. |
| `Script function not found: doPost` | You deployed the wrong version. Deploy again with `Code.gs` present. |
| Everything returns HTML | The URL is the editor URL, not the `/exec` Web App URL. |
| Duplicates in the sheet | Only possible if a row's `RecordID` was deleted. The backend treats `RecordID` as the identity. |

## Money looks wrong

* Totals are computed in cent-free floats with 2-decimal rounding at each step.
  If a total is off by a cent, check the discount: percent discounts round to the
  cent, then subtract.
* A combo counts as one sale but its components appear in the volume report. That
  is intended.

## Printing

| Symptom | Fix |
| --- | --- |
| Prints the whole page | Use the receipt dialog's **Print receipt** button, which applies the print stylesheet. |
| Receipt is blank | Some browsers block print from `file://`. Use the packaged Windows build. |

## Building

See the **Common problems** table at the end of `03-android-build.md` and
`04-windows-build.md`.

## Starting over

```bash
# browser: clear the site data for the page
# Android: Settings → Apps → {{APP_NAME}} → Storage → Clear data
# Windows: delete %APPDATA%/{{APP_NAME}} (back up first!)
```
