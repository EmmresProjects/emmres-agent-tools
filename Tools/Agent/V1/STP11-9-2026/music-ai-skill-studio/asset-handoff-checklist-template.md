# Handoff review - {{case_title}}

Case ID: `{{case_id}}`  ·  Kind: {{kind}}  ·  Generated: {{timestamp_utc}}
Reviewer: ______________________  Role: ______________________  Date: ____________

Use this sheet for the human sign-off that the studio's generated checklist cannot
perform. Copy it next to the delivery, fill it in, and attach it to the handoff.

## 1. Package integrity

- [ ] Delivery archive opens cleanly from a fresh extraction into an empty folder.
- [ ] Every file listed in the handoff manifest is present, spelled exactly as listed.
- [ ] No stray render, temp, or `.tmp` file is in the package.
- [ ] File names match the naming convention agreed for this project.

## 2. Technical verification

- [ ] `qc-audio` run on every delivered WAV; every file reports PASS or the exception is documented.
- [ ] Sample rate, bit depth, channel count match the delivery profile.
- [ ] True peak and integrated loudness measured with a dedicated meter (the studio does not measure LUFS).
- [ ] No audible clicks at edit points, loop points, or region boundaries.
- [ ] Silence head/tail is intentional (or removed with `trim-silence`).

## 3. Listening verification

- [ ] Phone speaker, headphones, car/mono check completed.
- [ ] Vocal/lead element intelligible in every section.
- [ ] No unintended artifacts, dropouts, or phase weirdness.
- [ ] Reference comparison notated (traits matched, not copied).

## 4. Rights, consent, disclosure

- [ ] Rights matrix row exists for every asset, sample, loop, and voice in the delivery.
- [ ] Voice consent scope covers this use, this territory, and this term.
- [ ] AI-assisted elements disclosed in the platform's required wording.
- [ ] Third-party samples cleared or replaced.
- [ ] No imitation of a named living artist requested or shipped.

## 5. Reproducibility and recovery

- [ ] Prompts, seeds, model names, and settings recorded for generated material.
- [ ] Session files, stems, and MIDI/notation preserved.
- [ ] A known-passing reference copy is stored outside the delivery.
- [ ] Restore/rollback path tested; the previous approved version is recoverable.

## 6. Sign-off

- [ ] Producer approval recorded in the decision log.
- [ ] Approval tokens used (if any): ______________________
- [ ] Release date and recipient confirmed.

Notes / exceptions:

```
(record every deviation here; a blank field is not a pass)
```
