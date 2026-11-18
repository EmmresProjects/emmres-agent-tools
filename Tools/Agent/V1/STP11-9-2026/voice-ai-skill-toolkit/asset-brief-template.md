# Voice Work Brief — {{SKILL_NAME}}

- Skill ID: `{{SKILL_ID}}`  (catalog {{SKILL_NUMBER}})
- Category: {{CATEGORY_NAME}} (category {{CATEGORY_ID}}, tier: **{{TIER}}**)
- Prepared: {{DATE}}
- Requested by: {{REQUESTER}}
- Objective: {{OBJECTIVE}}

## 1. Task definition and non-goals

{{OBJECTIVE}}

- This brief covers only the catalog skill above. It does not authorise publishing, distribution, financial commitments, or credential handling; those require separate human approval.
- Anything not listed in Inputs below is out of scope until agreed in writing.

## 2. Inputs and constraints

| Constraint | Value |
|---|---|
| Language / locale | {{LANGUAGE}} |
| Delivery profile | {{DELIVERY_PROFILE}} |
| Sample rate | {{SAMPLE_RATE}} Hz |
| Channels | {{CHANNELS}} |
| Speaking style preset | {{PRESET}} |
| Speaker authorization | {{SPEAKER_AUTH}} |

Accepted inputs: normalized text or script, source recordings (if any), pronunciation
lexicon, reference prompts, consent record (when the tier is elevated), delivery
specification, and the safety policy (asset-safety-policy.md).

Non-goals: no deployment, no model release, no public posting, no payment, no
credential use, and no third-party data access — request approval separately.

## 3. Source catalog entry

> {{DESCRIPTION}}

Focus area: {{FOCUS}}.

## 4. Workflow (recommended construction pattern)

1. Define the voice task, trigger phrases, accepted inputs, output formats, and non-goals.
2. Specify language, locale, pronunciation, speaker authorization, emotion, speaking rate, latency, sample rate, channel layout, and delivery target.
3. Keep editable intermediate outputs: normalized text, phonemes, timestamps, transcripts, stems, cue sheets, approval records.
4. Use reference files for pronunciation, locales, safety policy, delivery profiles, provider contracts, benchmark definitions.
5. Use deterministic scripts for analysis, conversion, segmentation, metadata, conformance and regression checks.
6. End with listening review, technical measurement, consent and privacy review, accessibility checks and clean-state handoff.

## 5. Acceptance criteria (quality dimensions)

The output is acceptable only when each dimension below is explicitly reviewed and signed off:

- [ ] Intelligibility — speech/labels understood on first pass in the target channel
- [ ] Naturalness — prosody and rate appropriate to the delivery profile
- [ ] Latency — within the budget of the target use case (document the budget)
- [ ] Privacy — no personal data beyond what the task requires
- [ ] Consent — authorization documented; see Section 6 when tier is elevated
- [ ] Technical quality — format, loudness, clipping, silence checks pass (`audio-check`)
- [ ] Accessibility — reachable by the intended audience (captions/transcripts where applicable)
- [ ] Delivery — matches the delivery profile and handoff checklist

## 6. Consent, privacy and approval gate

Tier: **{{TIER}}**
{{CONSENT_GATE}}

## 7. Deliverables and traceability

Produce, at minimum, inside the work-package folder:

- `work-package.json` — manifest validated against asset-work-package-schema.json
- normalized / source text used for synthesis or review
- transcript (SRT/VTT/JSON) when audio is produced or consumed
- consent-record.json when consent is required
- QA report and human sign-off (use `qa-report` after `validate-package`)

## 8. Recovery and rollback

- Keep original source recordings untouched; work on copies.
- If validation fails, fix the failing artifact and re-run `validate-package` — never edit the QA verdict by hand.
- If consent is revoked, stop delivery, quarantine derived artifacts, and follow the revocation process in the consent record.
- If a defect is found after handoff, open an incident, notify the approver chain, and re-issue from the last approved manifest.

## 9. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Producer |  |  |  |
| Reviewer |  |  |  |
| Approver |  |  |  |
