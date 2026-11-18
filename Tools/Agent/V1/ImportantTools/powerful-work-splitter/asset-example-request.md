<!--
  asset-example-request.md
  Purpose: a realistic, deliberately messy request used by the live demo and the
  regression tests. It contains HTML tags, CSS classes, data attributes, nested
  numbering marks, and extra spacing so that the normalizer can be exercised on
  the same kind of input a real user pastes from a rendered catalog page.

  Try it with:
    python3 tools-work-splitter.py plan --request powerful-work-splitter/asset-example-request.md --count 4
-->

<div class="skill-card" data-skill-id="cat-1" data-rank="A+">
  <h2 class="cat-title">1. Orchestration, Planning, and Work Splitting</h2>

  <ol class="skill-list">
    <li class="skill" data-id="pws">
      <span class="mark">1.</span>
      <span class="name">Powerful Work Splitter</span>
      <p class="desc">Evaluates every request before execution and decides whether one response is sufficient or staged work is safer. It scores scope, file volume, dependencies, tool calls, uncertainty, and verification burden; recommends a prompt count; asks the user to accept or override that count.</p>
    </li>

    <li class="skill" data-id="feas">
      <span class="mark">2.</span>
      <span class="name">Single-Prompt Feasibility Assessor</span>
      <p class="desc">Determines whether a task can be completed well in one response.</p>
    </li>

    <li class="skill" data-id="cpc">
      <span class="mark">5.</span>
      <span class="name">Checkpoint and Continue Controller</span>
      <p class="desc">Stops at safe boundaries and resumes only after the user signals continuation. It recognizes Continue, proceed, go on, next, and equivalent intent while rejecting ambiguous scope-changing replies.</p>
    </li>
  </ol>

  <section class="assets">
    <h3>Attached files</h3>
    <ul>
      <li>quarterly-figures.xlsx</li>
      <li>brand-notes.md</li>
      <li>quarterly-figures-v2.xlsx</li>
    </ul>
  </section>

  <section class="deliverables">
    <h3>Deliverables</h3>
    <ul>
      <li>12-page investor briefing PDF that reopens cleanly</li>
      <li>Page-by-page storyline with one thesis per page</li>
      <li>Reconciliation table for every reported figure</li>
    </ul>
  </section>

  <section class="constraints">
    <h3>Constraints</h3>
    <ul>
      <li>Must use the attached figures only</li>
      <li>Tone: board-ready, no more than 60 words of body copy per page</li>
      <li>Do not redesign the brand</li>
    </ul>
  </section>
</div>
