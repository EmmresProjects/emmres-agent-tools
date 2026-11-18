# Emmre's Agent Tools

A structured library of agent tools, capability packages, assets, examples, tests, and documentation.

## Add tools

Place each tool in `Tools/CATEGORY/TOOL-NAME/`. Start by copying `Templates/Tool-Package/`.

Every tool should include:
- `README.md`
- `tool.yaml`
- source files in `src/`
- supporting files in `assets/`
- examples in `examples/`
- tests in `tests/`

## Documentation

The `docs/` folder is the GitHub Pages knowledge site. Manually update `docs/index.md`, `docs/catalog.md`, and the relevant category page whenever a tool is added.

## Large files

Large binary assets can use Git LFS. Documentation, source code, YAML, JSON, XML, and Markdown should normally remain in regular Git.
