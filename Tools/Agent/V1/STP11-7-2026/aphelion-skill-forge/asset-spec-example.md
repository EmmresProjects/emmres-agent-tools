# Example capability specification (Aphelion Skill Forge input format)

Paste this file, or any part of it, into the forge to see how parsing behaves.
The forge accepts Markdown, plain text, copied HTML and JSON, and ignores
numbering marks, tags, CSS classes, data attributes and extra spacing.

## Turnaround Reporting and Evidence

Focus: Weekly operating evidence for delivery reviews, with sourced metrics,
stated assumptions, and a reviewer-ready package.

1. Skill Name: Weekly Metrics Compiler
Description: Collects the week's delivery metrics from declared sources,
reconciles totals against the prior week, and reports every metric with its
source document and extraction date. It refuses to publish a metric whose
source cannot be named, and lists the gap instead of guessing.

2. Skill Name: Evidence Pack Assembler
Description: Assembles the supporting extracts behind each reported metric into
one reviewer-ready pack, keeping page numbers, table captions and provenance
intact so a reviewer can trace a number back to its origin in two steps.

3. Skill Name: Assumption Register Keeper
Description: Records every assumption and estimate used in the report with an
owner, a date and a confidence note, and flags any assumption that silently
changed since the previous cycle.

4. Skill Name: Variance Narrative Writer
Description: Explains material week-on-week variance in plain language,
separating measured movement from estimated movement, and marks any claim that
still needs a source as unverified rather than presenting it as fact.

5. Skill Name: Review Readiness Checker
Description: Runs a final pass over the report package, checking that every
metric has provenance, every assumption has an owner, and every open question
is listed, before the package goes to a human reviewer for approval.
