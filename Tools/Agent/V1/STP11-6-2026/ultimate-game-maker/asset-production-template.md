# {{TITLE}}

Umbrella Skill Tool: `{{UMBRELLA}}`  
Selection mode: `{{SELECTION_MODE}}`  
Category handlers: **{{HANDLER_COUNT}}**  
Named specialists: **{{SKILL_COUNT}}**  
Version status: `{{VERSION_STATUS}}`  
Generation: `{{PROVIDER}}` / `{{MODEL_PROFILE}}` / `{{PROMPT_SET}}`

## 1. Purpose and brief

Goal: {{GOAL}}

Genre: `{{GENRE}}`  
Engine: `{{ENGINE}}`  
Language: `{{LANGUAGE}}`  
Target platform: `{{PLATFORM}}`  
Seed: `{{SEED}}`

This plan unifies every selected category and its listed specialists into one editable production handoff. It follows the source catalog contract: inputs, outputs, controls, checks, recovery, and interoperability are explicit. It does not create a parent `SKILL.md`, does not call external game engines, and does not perform publishing or deployment.

## 2. Acceptance criteria

{{ACCEPTANCE_CRITERIA}}

## 3. Constraints and references

{{CONSTRAINTS}}

## 4. Inputs and interoperability

Required future-skill inputs: game goal, genre, engine, exact version, language, target platform, assets, references, constraints, repository, build environment, and acceptance criteria.

Version evidence status: `{{VERSION_STATUS}}`. Missing evidence: {{MISSING_EVIDENCE}}.

Interoperability fields that must be locked before an external build: engine and editor version, SDKs, compiler, language standard, packages, file formats, coordinate systems, units, input, networking, and build tooling. See `interoperability.json` and `version-notes.md` in this output directory.

## 5. Outputs and controls

Actual generated files:

{{OUTPUT_FILES}}

Controls recorded in this plan: mechanics, platform, quality, performance budget, art and audio systems, networking, save behavior, difficulty, content scope, and compatibility targets. The `capability-packets.json` and `capability-packets.md` files carry the detailed per-specialist contract.

## 6. Category handlers

{{HANDLER_TABLE}}

## 7. Capability explanations

{{CAPABILITY_SECTIONS}}

Full compact capability list:

{{COMPLETE_CAPABILITY_LIST}}

## 8. Checks and tests

Checks are defined in `acceptance-checks.json` and `test-plan.md`. They can fail, cover clean startup and gameplay paths, include structured validation, and include the local HTML5 logic harness when the `html5` profile is selected. External compilation, device certification, performance capture, and store validation are documented as checklists; they are not performed by this local tool.

## 9. Recovery

State for local recovery is in `recovery-state.json`. If an output file is missing, rerun:

`python3 ultimate-game-maker/tools-game-maker.py recover --state <output>/recovery-state.json --brief <brief.json>`

Preserve passing work, isolate the earliest failing layer, repair the smallest component, and rerun linked checks from a clean output path. Ordered recovery steps:

{{RECOVERY_STEPS}}

## 10. Safety and approval gates

No destructive, irreversible, privileged, publishing, financial, credential, or security-sensitive action is executed. Approval is required before any such action:

{{APPROVAL_GATES}}

Use `python3 ultimate-game-maker/tools-game-maker.py gate --action <action> --reason "<human review>" --confirm APPROVE` to record a local human-review receipt. The receipt never performs the external action.

## 11. Limits and unavailable integrations

{{LIMITATIONS}}

## 12. Handoff

The generated Markdown and JSON files are editable sources. `dependency-lock.json` records deterministic local generation and external version fields that must be supplied. `build-instructions.md` gives exact local commands and clearly separates external engine steps that require the user's licensed runtime, SDK, or compiler. `recovery-state.json` is an operational recovery file, not a complete skill manifest.
