# Emmre's Agent Tools
Add any folder under `Tools`, `Knowledge`, or `Skills`. It becomes a category automatically. `Skills/General` is included. Nested folders are preserved.

Run `1. Update Index.cmd`, then `2. Push to GitHub LFS.cmd`.

The updater avoids `py.exe` because stale registrations can point to deleted paths. For a custom Python location, set `EMMRES_PYTHON` to its full path.

## Open versus Download
**Open** opens the asset in a new browser tab. **Download** uses JavaScript `fetch()` and a generated Blob URL to trigger the browser's save operation, even for file types such as Markdown, text, images, PDFs, audio, and video that browsers normally preview inline. JavaScript must be enabled on the GitHub Pages site.
