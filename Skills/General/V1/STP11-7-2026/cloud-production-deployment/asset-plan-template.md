# {{PLAN_TITLE}}

**Generated:** {{GENERATED_AT}} by {{GENERATED_BY}}
**Provider:** {{PROVIDER}} · **Environment:** {{ENVIRONMENT}} · **RPO:** {{RPO_MINUTES}} min · **RTO:** {{RTO_MINUTES}} min
**Source catalog:** Cloud, Server, and Production Deployment — Skill Catalog, Edition 1.0 (November 2026)

## Scope

{{SCOPE}}

## Execution Phases

{{PHASES}}

## Approval Gates

{{APPROVAL_GATES}}

## Evidence Checklist

{{EVIDENCE_CHECKLIST}}

## Rollback Procedure

{{ROLLBACK}}

## Escalation Path

{{ESCALATION}}

## Core Production Acceptance (definition of done)

The deployment is complete only when every acceptance reference below holds, verified by evidence attached to the change record:

{{CORE_ACCEPTANCE}}

## Verification Command

Validate a deployment config for this service against the production acceptance rubric:

```
python3 tools-production-deployment.py assess --config <deployment-config.json> --format text
```

A `fail` verdict blocks release; a `warn` verdict requires documented, signed-off exceptions. See knowledge-readme.md for the config schema and scoring model.
