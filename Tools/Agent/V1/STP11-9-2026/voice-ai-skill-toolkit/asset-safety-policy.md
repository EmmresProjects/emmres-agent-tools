# Voice Safety, Consent and Governance Policy

Version 1.0.0 — applies to every work package produced with this toolkit.
The `validate-package` and `consent-check` commands enforce the machine-checkable
parts of this policy; the human parts are enforced by the approval gate.

## 1. Consent before cloning, always

- Never clone, synthesize, impersonate or derive a model from a real person's
  voice without a documented, signed consent record (see
  asset-consent-record-template.json).
- Similarity scores are quality metrics, not authorization. A perfect clone of an
  unauthorized voice is still a policy failure.
- Datasets must record provenance, license, and per-speaker consent or a lawful
  basis. "Found online" is not a basis.

## 2. Disclosure

- AI-generated or AI-modified voices disclosed to every audience that interacts
  with them, wherever technically feasible.
- Legal, financial, clinical and evidentiary content must disclose and route for
  qualified human approval.

## 3. Scope and revocation

- Consent is scoped: use case, language, territory, term, exclusivity. Work
  outside the recorded scope requires a new or amended record.
- Revocation requests must be honored within the lead time recorded in the
  consent record. On revocation: stop delivery, quarantine derived artifacts,
  delete or re-derive per the deletion_on_revocation flag.

## 4. Prohibited uses

- Impersonation for fraud, deception, defamation or harassment.
- Depicting real people saying things they did not say without explicit consent.
- Evading biometric identification or anti-spoofing controls.
- Diagnosing medical conditions or giving medical, legal or financial advice.
  Clinical and legal workflows are documentation and dictation support only.
- Generating CSAM-adjacent or otherwise unlawful content; this toolkit does not
  assist with such work under any configuration.

## 5. Data protection

- Voice recordings and derived embeddings are personal data. Minimize
  collection, encrypt at rest where offered, set retention limits, honor
  deletion requests.
- Do not place credentials, API keys or private endpoints in manifests,
  briefs, transcripts or reports.

## 6. Approval gates

Work packages in `elevated` tiers (catalog categories 10, 11, 12, 35, 36, 37)
must show a consent record and at least one human approval with decision
`approved` before status may be `approved` or `delivered`. `validate-package`
fails the package otherwise.

## 7. Human review is not optional

Listening review, technical measurement, consent/privacy review, accessibility
checks and clean-state handoff are part of the construction pattern. A tool pass
is evidence for reviewers, not a replacement for them.

## 8. Escalation

Escalate to the designated approver (producer, legal, privacy officer) when:
scope is unclear, consent is disputed, a dataset's provenance is unverifiable,
an output could be mistaken for a real person without authorization, or any
artifact in the package fails validation twice.
