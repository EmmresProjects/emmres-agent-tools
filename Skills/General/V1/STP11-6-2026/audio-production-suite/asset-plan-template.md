# Production plan: {{TITLE}}

Generated {{GENERATED_AT}} by the Audio Production Suite planner. Placeholders in this
template are filled from the brief, the capability catalog, the selected delivery preset,
and the QA policy.

## 1. Brief

| Field | Value |
| --- | --- |
| Goal | {{GOAL}} |
| Audience | {{AUDIENCE}} |
| Medium | {{MEDIUM}} |
| Delivery preset | {{PRESET_LABEL}} (`{{PRESET_ID}}`) |
| Target duration | {{DURATION}} |
| Deadline | {{DEADLINE}} |
| Rights status | {{RIGHTS}} |
| Constraints | {{CONSTRAINTS}} |

## 2. Routed capabilities ({{CAPABILITY_COUNT}})

| Id | Capability | Umbrella category | Toolkit operations |
| --- | --- | --- | --- |
{{CAPABILITY_ROWS}}

## 3. Production stages

{{STAGES}}

## 4. Controls to expose

{{CONTROLS}}

## 5. Objective checks and review gates

{{CHECKS}}

## 6. Measurable metrics to report

{{METRICS}}

## 7. Delivery targets

{{TARGETS}}

### Container and format

{{FORMAT}}

## 8. Deliverables

{{DELIVERABLES}}

## 9. Assumptions

{{ASSUMPTIONS}}

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
{{RISKS}}

## 11. Approval gates

| Action id | Action | When it fires | Who approves |
| --- | --- | --- | --- |
{{APPROVALS}}

## 12. Limits of this toolkit

{{LIMITS}}

## 13. Suggested command sequence

```sh
{{COMMANDS}}
```

Review gate: re-run `analyze` and `verify-manifest` after any change to an approved
deliverable, and record the new result before sign-off.
