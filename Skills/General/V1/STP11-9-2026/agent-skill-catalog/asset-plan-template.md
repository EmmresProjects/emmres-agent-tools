# {{skill_name}} — implementation brief

| Field | Value |
| --- | --- |
| Skill | {{skill_name}} |
| Suggested folder name | {{skill_slug}} |
| Catalog category | {{category_index}}. {{category}} |
| Catalog source | {{catalog}} |
| Brief generated | {{generated}} |

## Capability

{{description}}

## Trigger phrases

{{trigger_phrases}}

## Non-triggers

{{non_triggers}}

## Inputs

{{inputs}}

## Procedure

{{steps}}

## Outputs

{{outputs}}

## Approval gates

{{approval_gates}}

## Verification checklist

{{verification}}

## Limits and recovery

{{limits}}

## Next commands

{{next_commands}}

## Notes for the implementing agent

- Keep this brief as the control plane; move long reference material into `asset-` files.
- Deterministic steps belong in a `tools-` script, not in prose.
- Preserve the last known passing state before editing; keep a rollback path.
- Do not add capabilities to the description that the implementation does not perform.
