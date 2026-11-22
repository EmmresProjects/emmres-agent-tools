# Emmre's Agent Tools
Add any folder under `Tools`, `Knowledge`, or `Skills`. It becomes a category automatically. `Skills/General` is included. Nested folders are preserved.

Run `1. Update Index.cmd`, then `2. Push to GitHub LFS.cmd`.

The updater avoids `py.exe` because stale registrations can point to deleted paths. For a custom Python location, set `EMMRES_PYTHON` to its full path.

## Open versus Download
**Open** opens the asset in a new browser tab. **Download** uses JavaScript `fetch()` and a generated Blob URL to trigger the browser's save operation, even for file types such as Markdown, text, images, PDFs, audio, and video that browsers normally preview inline. JavaScript must be enabled on the GitHub Pages site.

## Local download preview
Do not double-click `docs/downloads.html` directly. A `file://` page is blocked from fetching repository files by browser security. Run `1. Update Index.cmd`; it rebuilds the catalog, starts a local HTTP server on `127.0.0.1:8765`, and opens the working downloads page. The GitHub Pages version already runs over HTTPS and needs no local server.

## V5 path correction
Generated file links are relative to the deployed site root, such as `Skills/General/...`, not `../Skills/General/...`. The local updater builds `_site_preview` with the same layout as the GitHub Pages artifact, so local and published Open/Download links use the same paths.
