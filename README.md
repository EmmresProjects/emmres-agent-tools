# Emmre's Agent Tools

A self-updating public library for reusable tools, knowledge, and skills.

## Add content

- Put tools under `Tools/<Category>/...`
- Put references and knowledge under `Knowledge/<Category>/...`
- Put complete reusable skill folders under `Skills/<Category>/<Skill Name>/...`

You can add any new category folder. The index generator discovers it automatically.

## Commands

1. Run `1. Update Index.cmd` to rebuild `docs/index.html`, section pages, downloads, and `docs/catalog.json`.
2. Run `2. Push to GitHub LFS.cmd` to rebuild, commit, and push everything with Git LFS.

## GitHub Pages

In repository settings, set **Pages > Source** to **GitHub Actions**. The workflow builds the index and deploys `docs`, `Tools`, `Knowledge`, and `Skills` into one Pages artifact, so every indexed link remains on the same public website.

Readable text and code can be searched as knowledge. Binary tools, models, media, and Office files are downloadable assets. Git LFS storage and bandwidth quotas still apply.
