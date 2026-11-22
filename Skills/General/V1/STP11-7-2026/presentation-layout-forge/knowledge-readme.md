# Presentation Layout Forge

## Purpose

`presentation-layout-forge` is a reusable Skill Tool component for learning a presentation's visual construction and producing clean, editable PowerPoint slides. It focuses on the parts that usually fail in generated decks: placement, resizing, line routing, UML structure, process diagrams, tables, text fit, and overlap checks.

The tool learns measurable layout signals from a source PPTX, converts them into a reusable profile, builds new PPTX files from a JSON specification, and checks the result before delivery. The default preset uses the clear academic technical style learned from the supplied reference deck: 10 × 5.625 inch slides, 0.4 inch outer margins, top-left titles, white backgrounds, thin black lines, black table headers, alternating gray rows, aligned class boxes, short orthogonal connectors, and shared implementation buses.

## Capabilities

1. **Inspect PPTX**: extract slide size, layout name, shape type, text, and exact geometry.
2. **Profile style**: collect common fonts, font sizes, colors, margins, table sizes, and recommended defaults.
3. **Validate layout**: find objects outside the slide, likely text overflow, shape collisions, and connectors crossing solid shapes.
4. **Build editable decks**: create title, section, bullet, table, flow, and UML slides from JSON.
5. **Route diagrams cleanly**: use tiered UML layout, orthogonal routes, short connectors, and one shared dashed implementation bus.
6. **Build tables consistently**: use weighted column widths, black headers, white header text, alternating gray rows, and a separate note line.
7. **Create process diagrams**: use a snake layout with stable box sizes and short horizontal or vertical connectors.
8. **Structured output and errors**: every command returns JSON; failures use nonzero exit codes and a JSON error object.

## Files

- `presentation-layout-forge/tools-presentation-layout-forge.py`: primary CLI entry point. Handles commands, validation, timeouts, cancellation, input checks, output overwrite protection, and structured results.
- `presentation-layout-forge/tools-layout-core.py`: reusable PPTX inspection, profiling, validation, geometry, chart-free diagram construction, table construction, and slide creation logic.
- `presentation-layout-forge/tools-requirements.txt`: supported Python dependency range.
- `presentation-layout-forge/asset-style-preset.json`: reusable visual defaults and connector policies.
- `presentation-layout-forge/asset-layout-spec.schema.json`: JSON Schema for deck specifications.
- `presentation-layout-forge/asset-example-spec.json`: working example covering UML, flow, table, title, and editable output.
- `presentation-layout-forge/knowledge-readme.md`: this operational guide.

## Dependencies

- Python 3.10 or newer
- `python-pptx` 1.0.2 or newer, below 2.0

Install with an explicit shell:

```bash
python -m pip install -r presentation-layout-forge/tools-requirements.txt
```

The tool does not call a network service, use credentials, run arbitrary shell commands, or publish files.

## Primary entry point

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py --help
```

List capabilities:

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py capabilities
```

## Commands

### Inspect a PPTX

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py inspect \
  --input input.pptx \
  --output inspection.json
```

Output fields include slide size, slide count, layout name, shape count, shape type, coordinates in inches, size in inches, and visible text.

### Learn a reusable profile

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py profile \
  --input reference.pptx \
  --output learned-profile.json
```

The profile reports observed fonts, sizes, colors, margins, table geometry, and recommended defaults. The report does not silently edit the packaged preset. Review the profile, then copy approved values into `presentation-layout-forge/asset-style-preset.json`.

### Validate a PPTX

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py validate \
  --input deck.pptx \
  --output validation.json
```

Validation checks:

- Object bounds against the 10 × 5.625 inch slide
- Estimated text fit based on font size, box width, box height, and line count
- Meaningful overlap between solid shapes
- Connector bounding paths that pass through the interior of a solid shape

`valid: true` means no error-level issue was found. Warnings still need review because a geometry-only test cannot fully replace visual rendering.

### Build a PPTX

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py build \
  --spec presentation-layout-forge/asset-example-spec.json \
  --preset presentation-layout-forge/asset-style-preset.json \
  --output output.pptx \
  --overwrite
```

Optional arguments:

- `--template template.pptx`: append generated slides to a template deck.
- `--timeout 120`: stop a build that exceeds the time limit.
- `--overwrite`: replace an existing output. Without this flag, the tool refuses to overwrite.

## Input specification

The root object contains a non-empty `slides` array. Each slide needs `type` and `title`.

Supported slide types:

- `title`: `title`, optional `subtitle`
- `section`: `title`
- `bullets`: `title`, `items`, optional `font_pt`
- `table`: `title`, `headers`, `rows`, optional `widths`, `height`, `font_pt`, `note`
- `flow`: `title`, `steps`, optional `columns`, `box_w`, `box_h`, `note`
- `uml`: `title`, `nodes`, `relationships`, optional `note`

### UML nodes

```json
{
  "id": "command",
  "label": "ICommand",
  "stereotype": "<<interface>>",
  "tier": 0,
  "w": 2.2,
  "h": 0.9,
  "body": ["+ Execute()"]
}
```

- `id` must be unique.
- `tier` controls the row. Lower tier values appear higher.
- `w` and `h` are inches.
- `body` is an array of editable text lines.

### UML relationships

```json
{"from":"repair","to":"command","kind":"implements"}
```

`implements` relations share one dashed bus. Other relations use direct horizontal links for same-row nodes or a three-segment orthogonal route for different rows.

### Flow steps

```json
{"title":"1. Input","body":"Validate the source."}
```

Flow steps use a snake layout. This keeps connectors short and avoids long lines across the slide.

### Table widths

`widths` are relative weights, not inches. For `[3.6, 2.2, 3.4]`, the tool divides the available width proportionally.

## Layout principles encoded by the tool

- Reserve the title band before placing content.
- Keep an outer margin of about 0.4 inches.
- Align related shapes to a shared row or column.
- Use consistent class header heights and body boundaries.
- Use shared buses for repeated implementation relations.
- Route connectors around boxes, not through them.
- Keep relationship labels in empty corridors.
- Prefer one clean flow direction.
- Use black headers and alternating gray rows for dense technical tables.
- Use editable PowerPoint shapes and tables instead of flattening diagrams into images.
- Reduce font size only after resizing or rearranging shapes has been considered.

## Safety and approval rules

The tool only reads local PPTX and JSON files and writes local JSON or PPTX outputs.

- Existing output files are never replaced unless `--overwrite` is provided.
- No publishing or upload action exists.
- No destructive slide deletion command exists.
- No arbitrary code, shell, macro, VBA, external model, or network execution exists.
- Cancellation with Ctrl+C returns exit code 130.
- Build timeout returns exit code 124.
- Temporary output is not stored inside the Skill Tool folder.

## Limits

- Output is PPTX only. PDF, DOCX, SVG, and web export are not implemented.
- The tool creates editable native shapes, tables, and connectors, but does not execute Microsoft PowerPoint.
- Text-fit checks are estimates. Font substitution can change rendering on another machine.
- Complex graph optimization, force-directed graphs, curved edges, image generation, SmartArt, animations, and 3D charts are unavailable.
- Template mode appends generated slides. It does not infer or replace arbitrary existing placeholders.
- The profile command extracts measurable patterns but does not claim to understand subjective design quality.
- Hosted resource failures caused by catalog, permission, or platform access are outside the package.

## Troubleshooting

### `ModuleNotFoundError: pptx`

Run:

```bash
python -m pip install -r presentation-layout-forge/tools-requirements.txt
```

### Output already exists

Use a new path or explicitly approve replacement:

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py build \
  --spec presentation-layout-forge/asset-example-spec.json \
  --output output.pptx \
  --overwrite
```

### Diagram is too dense

- Add a tier.
- Shorten body text.
- Increase node width and reduce nodes per tier.
- Split one diagram into architecture and flow slides.
- Run `validate` after each change.

### Connector warning

Move nodes into separate tiers or add space between rows. For repeated inheritance or interface implementation, use `kind: "implements"` so the tool creates one shared dashed bus.

### Text warning

Increase `w` or `h`, shorten the text, reduce `font_pt`, or split content across slides. Do not solve every text warning by shrinking the font.

## Recovery workflow

1. Preserve the last passing PPTX.
2. Run `validate` and inspect the first affected slide.
3. Change one node size, tier, column width, or font value.
4. Build to a new output path.
5. Validate the new file.
6. Open or render the file in the target presentation environment before replacing the passing version.

## Extension guidance

- Add a new slide type as `build_<type>` in `tools-layout-core.py` and add the type to the CLI allowlist and schema.
- Keep deterministic geometry in `tools-layout-core.py`.
- Keep command parsing, timeouts, overwrite approval, and JSON responses in `tools-presentation-layout-forge.py`.
- Add reusable presets as `asset-*.json` files only when they are used by a real workflow.
- Preserve direct references from the future skill root, such as `presentation-layout-forge/asset-style-preset.json`.

## Tests completed

The following real interface tests were completed:

- Main `--help` command
- `capabilities` command
- `inspect` on the supplied reference PPTX
- `profile` on the supplied reference PPTX
- `validate` on the supplied reference PPTX
- `build` using `asset-example-spec.json`
- `validate` on the generated PPTX
- Missing input rejection
- Existing output rejection without `--overwrite`
- Recovery by rebuilding with `--overwrite`
- Changed specification producing changed slide content
- Clean temporary skill root test with one adjacent test `SKILL.md`
- Parsing and reopening the generated PPTX with `python-pptx`
- Google Apps Script syntax check with Node.js

The tests do not claim Microsoft PowerPoint was launched.

## Microsoft PowerPoint and Google Slides option catalog

The packaged `presentation-layout-forge/asset-format-options-catalog.json` standardizes the UI and design choices used for professional diagrams. The catalog covers PowerPoint connector types, line widths, dash patterns, compounds, caps, joins, transparency, begin and end arrow types, arrow sizes, fills, effects, merge operations, order, align, distribute, selection pane, size, position, rotation, text box margins, AutoFit, tables, and accessibility. It also covers the matching Google Slides line, arrow, elbow, curved, shape, arrange, Format options, table, text, and accessibility controls.

List all options:

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py options
```

List only PowerPoint options:

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py options --platform powerpoint
```

List only Google Slides options:

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py options --platform google-slides
```

Filter a category:

```bash
python presentation-layout-forge/tools-presentation-layout-forge.py options \
  --platform powerpoint \
  --category arrow-types
```

### PowerPoint operating map

- **Insert connectors**: `Insert > Shapes > Lines`, then choose Straight Connector, Elbow Connector, or Curved Connector.
- **Change connector route**: select the connector, use `Shape Format > Edit Shape > Reroute Connectors` where available, or drag the yellow route handle.
- **Line color and transparency**: `Shape Format > Shape Outline > More Outline Colors`, or `Format Shape > Line > Color / Transparency`.
- **Line width**: `Shape Format > Shape Outline > Weight`, or `Format Shape > Line > Width`.
- **Dash pattern**: `Shape Format > Shape Outline > Dashes`.
- **Begin and end arrow**: `Format Shape > Line > Begin Arrow type / End Arrow type`.
- **Arrow size**: `Format Shape > Line > Begin Arrow size / End Arrow size`, each with Small, Medium, or Large.
- **Compound, cap, and join**: `Format Shape > Line > Compound type / Cap type / Join type` in the desktop app where supported.
- **Shape fill**: `Shape Format > Shape Fill`, or `Format Shape > Fill` for solid, gradient, picture or texture, pattern, no fill, and slide background fill.
- **Shape effects**: `Shape Format > Shape Effects` for shadow, reflection, glow, soft edges, bevel, and 3-D rotation.
- **Exact size and position**: `Shape Format > Size`, or `Format Shape > Size & Properties` for height, width, rotation, scale, lock aspect ratio, left, and top.
- **Text inside shapes**: `Format Shape > Text Options > Text Box` for direction, vertical alignment, margins, wrapping, and AutoFit.
- **Arrange**: `Shape Format > Arrange` for order, align, distribute, group, rotate, and Selection Pane.
- **Merge shapes**: select shapes, then `Shape Format > Merge Shapes` for Union, Combine, Fragment, Intersect, and Subtract.
- **Tables**: use `Table Design` for table styles, shading, and borders; use `Layout` for rows, columns, merge, split, distribute, cell margins, text direction, and alignment.

Use the full PowerPoint desktop app for the deepest Format Shape controls. PowerPoint web and mobile have fewer formatting controls and may not expose the complete Format Shape pane.

### Google Slides operating map

- **Insert lines and connectors**: use the toolbar Line menu for Line, Arrow, Elbow connector, Curved connector, Curve, Polyline, and Scribble.
- **Format a selected line**: use Line color, Line weight, Line dash, Line start, and Line end on the toolbar.
- **Format a selected shape**: use Fill color, Border color, Border weight, and Border dash.
- **Size, rotation, position, shadow, reflection, and alt text**: use `Format > Format options` or right-click `Format options` when available.
- **Arrange**: use `Arrange` for order, align, center on page, distribute, group, rotate, and flip.
- **Tables**: right-click cells for row, column, merge, and unmerge operations; use toolbar controls for fill, border, and text.
- **Text**: use the toolbar and `Format` menu for font, size, emphasis, color, align, line spacing, paragraph spacing, bullets, numbering, indent, capitalization, and links.

`presentation-layout-forge/tools-google-slides-layout.gs` is a Google Apps Script helper for standardizing titles, shapes, lines, arrowheads, and tables. Paste it into `Extensions > Apps Script`, save, run `listLayoutForgeCapabilities`, and then run `runLayoutForge`. Google authorization is required by the host on first run. The script was syntax checked locally, but a Google Slides host and account were unavailable, so live Slides execution was not performed.

## Diagram production defaults

For technical decks, start with these values and change only when the source design requires it:

- Connector route: elbow or segmented orthogonal
- Begin arrow: none
- End arrow: medium triangle
- Normal relationship line: solid, 1 pt, black
- Implementation or realization line: dashed, 1 pt, black, shared bus
- Outline: black, 1 pt
- Shape fill: white
- Relationship label: 6.5 to 8 pt in a clear corridor
- UML body text: 7 to 9 pt
- Minimum shape gap: 0.18 inch
- Table header: black fill, white text
- Alternating table row: light gray `#D9D9D9`
- Resize the shape or split the slide before shrinking text below 7 pt

The tools intentionally favor clean geometry over visual effects. Curved connectors, shadows, reflection, glow, bevel, and 3-D formatting are available in the application catalog but are not default choices for technical diagrams.
