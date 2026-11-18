#!/usr/bin/env node
/**
 * Node.js companion static and syntax analyzer for JavaScript, ESM/CJS, TypeScript-flavored JS,
 * and package.json manifests. Invoked with bounded argv and no network access.
 */
import fs from "node:fs";
import vm from "node:vm";

function analyzeSource(source, filename = "snippet.mjs") {
  const findings = [];
  const imports = [];
  const exportsList = [];

  for (const match of source.matchAll(/^\s*import\s+(.+?)\s+from\s+['"]([^'"]+)['"]/gm)) {
    imports.push({ clause: match[1].trim(), module: match[2] });
  }
  for (const match of source.matchAll(/\brequire\(\s*['"]([^'"]+)['"]\s*\)/g)) {
    imports.push({ clause: "commonjs-require", module: match[1] });
  }
  for (const match of source.matchAll(/^\s*export\s+(?:async\s+)?(?:function|class|const|let|var|default)\s*([A-Za-z0-9_$]*)/gm)) {
    exportsList.push(match[1] || "default");
  }

  // Event-loop & security patterns
  if (/\bfor\s*\([^)]*\)\s*\{[\s\S]{0,240}?\bawait\b/.test(source)) {
    findings.push({
      rule_id: "JS-ASYNC-001",
      severity: "medium",
      message: "Sequential 'await' detected inside loop; consider Promise.all / Promise.allSettled with bounded concurrency."
    });
  }
  if (/\basync\s+function[\s\S]{0,400}?\b[A-Za-z0-9_]+Sync\s*\(/.test(source)) {
    findings.push({
      rule_id: "JS-LOOP-002",
      severity: "high",
      message: "Blocking *Sync API call inside async function risks starving the Node.js event loop."
    });
  }
  if (/\b(eval|new\s+Function)\s*\(/.test(source)) {
    findings.push({
      rule_id: "JS-SEC-001",
      severity: "high",
      message: "Dynamic code execution via eval/new Function detected."
    });
  }
  if (/\.innerHTML\s*=|dangerouslySetInnerHTML/.test(source)) {
    findings.push({
      rule_id: "JS-SEC-002",
      severity: "high",
      message: "Potential DOM XSS sink via innerHTML / dangerouslySetInnerHTML."
    });
  }

  // Syntax check: transform ESM keywords lightly to verify expression/statement parse in vm.Script
  let syntaxValid = true;
  let syntaxError = null;
  try {
    const transformed = source
      .replace(/^\s*import\s+[\s\S]*?from\s+['"][^'"]+['"]\s*;?/gm, "/* import */")
      .replace(/^\s*import\s+['"][^'"]+['"]\s*;?/gm, "/* side-effect import */")
      .replace(/^\s*export\s+default\s+/gm, "const __default_export__ = ")
      .replace(/^\s*export\s+(?=(?:async\s+)?(?:function|class|const|let|var)\b)/gm, "")
      .replace(/^\s*export\s*\{[^}]*\}\s*;?/gm, "/* named export */");
    new vm.Script(`(async () => {\n${transformed}\n})`, { filename });
  } catch (err) {
    syntaxValid = false;
    syntaxError = err && err.message ? String(err.message) : "Syntax error";
  }

  return {
    runtime: `node ${process.version}`,
    filename,
    line_count: source.split(/\r?\n/).length,
    syntax_valid: syntaxValid,
    syntax_error: syntaxError,
    imports,
    exports: exportsList,
    findings
  };
}

function analyzePackageJson(raw) {
  const pkg = JSON.parse(raw);
  const findings = [];
  if (!pkg.name || !pkg.version) {
    findings.push({ rule_id: "PKG-001", severity: "high", message: "package.json should declare both 'name' and 'version'." });
  }
  if (!pkg.engines || !pkg.engines.node) {
    findings.push({ rule_id: "PKG-002", severity: "low", message: "Consider pinning 'engines.node' for reproducible runtime compatibility." });
  }
  const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };
  for (const [dep, range] of Object.entries(deps)) {
    if (range === "*" || range === "latest") {
      findings.push({ rule_id: "PKG-003", severity: "high", message: `Unbounded dependency range '${range}' on '${dep}'.` });
    }
  }
  return {
    runtime: `node ${process.version}`,
    kind: "package.json",
    name: pkg.name || null,
    version: pkg.version || null,
    module_type: pkg.type || "commonjs",
    dependency_count: Object.keys(deps).length,
    scripts: Object.keys(pkg.scripts || {}),
    findings
  };
}

function main() {
  const args = process.argv.slice(2);
  let mode = "source";
  let filePath = null;
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === "--mode" && args[i + 1]) {
      mode = args[i + 1];
      i += 1;
    } else if (args[i] === "--file" && args[i + 1]) {
      filePath = args[i + 1];
      i += 1;
    }
  }
  if (!filePath || !fs.existsSync(filePath)) {
    process.stdout.write(JSON.stringify({ ok: false, error: "Missing or non-existent --file argument" }) + "\n");
    process.exit(2);
  }
  const content = fs.readFileSync(filePath, "utf8");
  const result = mode === "package" ? analyzePackageJson(content) : analyzeSource(content, filePath);
  process.stdout.write(JSON.stringify({ ok: true, result }) + "\n");
}

main();
