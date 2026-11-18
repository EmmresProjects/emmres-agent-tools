# ADR-{{number}}: {{title}}

- **Status:** {{status}}
- **Date:** {{date}}
- **Decision makers:** {{decision_makers}}
- **Supersedes:** {{supersedes}}

## Context

What forces are at play? State constraints explicitly: target platforms, minimum OS, offline boundary, toolchain pins, distribution route, team skills. Reference the catalog concept(s) that motivated this decision (e.g. `1.1` target platform decision matrix) and any related ADRs.

{{context}}

## Decision

The chosen option and the rejected alternatives with reasons. Record:

- Selected option (one sentence)
- Alternatives considered and why each was rejected
- Compatibility: platforms, minimum OS versions, CPU architectures
- Dependency weight (direct + transitive, license class, pinning status)
- Performance expectations and measurement plan
- Accessibility implications
- Distribution constraints (store policy, signing, notarization, update channel)
- Escape hatch to platform-specific code when the abstraction cannot express a need

{{decision}}

## Consequences

- What becomes easier / harder
- Reversibility: can this decision be undone without data loss? At what cost?
- Follow-ups: what must be done before this is load-bearing
- Offline impact: does this hold with the network blocked? If it depends on an external service, how is it verified and how does it degrade offline?

{{consequences}}

## Evidence

Artifacts that justify this decision: matrix output, benchmark traces, spike results, acceptance reports, policy references. Paths or immutable references only — never secrets.

{{evidence}}
