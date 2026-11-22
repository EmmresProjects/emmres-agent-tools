#!/usr/bin/env node
/**
 * tools-catalog-parser.mjs - secondary parser entry point for sheets-skill-library.
 *
 * Zero-dependency (Node 18+) companion to tools-spreadsheet-skill-library.py.
 * Use it when the host skill runs in a JavaScript environment, or when the
 * source list is messy HTML that benefits from a real regex-based DOM sweep.
 * It reads the same catalog shape and writes a catalog JSON the Python tool
 * accepts unchanged.
 *
 * Guarantees:
 *   - same surface: --help and `capabilities`
 *   - validates before writing, prints structured JSON errors, distinct exit codes
 *   - bounded input size, atomic write via temp file + rename
 *
 * Usage:
 *   node tools-catalog-parser.mjs --help
 *   node tools-catalog-parser.mjs capabilities
 *   node tools-catalog-parser.mjs extract --input source.html --out extracted-catalog.json
 *   node tools-catalog-parser.mjs verify  --catalog asset-skill-catalog.json
 */

import { readFileSync, writeFileSync, renameSync, statSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import process from 'node:process';

const VERSION = '1.0.0';
const TOOL = 'tools-catalog-parser';
const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const MAX_INPUT_BYTES = 4_000_000;
const MAX_CATEGORIES = 60;
const MAX_SKILLS_PER_CATEGORY = 40;

const EXIT_OK = 0;
const EXIT_INVALID = 2;
const EXIT_INTERNAL = 4;

const CAPABILITIES = [
  ['extract', 'Parse plain text, Markdown or HTML capability lists into catalog JSON.'],
  ['verify', 'Validate a catalog JSON file: ids, slugs, counts, required fields.'],
  ['capabilities', 'List the operations implemented by this parser.'],
];

const STOPWORDS = new Set([
  'and', 'or', 'of', 'the', 'for', 'a', 'an', 'with', 'to', 'in', 'on', 'by', 'into', 'from', 'as', 'at',
]);

class ToolError extends Error {
  constructor(code, message, hint = '', exitCode = EXIT_INVALID) {
    super(message);
    this.code = code;
    this.hint = hint;
    this.exitCode = exitCode;
  }
  toJSON() {
    return { ok: false, tool: 'sheets-skill-library', version: VERSION, parser: TOOL,
      error: { code: this.code, message: this.message, hint: this.hint, exit_code: this.exitCode } };
  }
}

function slugify(text) {
  return text
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^-|-$/g, '') || 'item';
}

function decodeEntities(text) {
  const map = { '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'", '&nbsp;': ' ' };
  return text
    .replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (m) => map[m] ?? m)
    .replace(/&#(\d+);/g, (_m, code) => String.fromCharCode(Number(code)))
    .replace(/\u00a0/g, ' ')
    .replace(/[\u2018\u2019]/g, "'")
    .replace(/[\u201c\u201d]/g, '"')
    .replace(/[\u2013\u2014]/g, '-');
}

function stripHtml(raw) {
  let text = raw
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ');
  for (const tag of ['p', 'li', 'ul', 'ol', 'div', 'section', 'article', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']) {
    text = text.replace(new RegExp(`</?${tag}\\b[^>]*>`, 'gi'), '\n');
  }
  text = text.replace(/<br\s*\/?>/gi, '\n');
  for (const tag of ['span', 'strong', 'em', 'b', 'i', 'u', 'a', 'code', 'mark', 'small', 'sup', 'sub', 'abbr', 'time', 'font', 'label', 'kbd', 's', 'del', 'ins']) {
    text = text.replace(new RegExp(`</?${tag}\\b[^>]*>`, 'gi'), '');
  }
  text = text.replace(/<[^>]+>/g, '');
  return decodeEntities(text);
}

const SKILL_LINE = /^\s*(?:[#>*\-]+\s*)*(?<num>\d{1,3})[.)]\s*(?:Skill\s+Name|Skill|Capability|Tool|Name)\s*[:\-]?\s*(?<name>.+?)\s*$/i;
const LABELLED_SKILL = /^\s*(?:[#>*\-]+\s*)*(?:Skill\s+Name|Skill|Capability)\s*[:\-]\s*(?<name>.+?)\s*$/i;
const DESC_LINE = /^\s*(?:[#>*\-]+\s*)*(?:Description|Desc|Details)\s*[:\-]?\s*(?<desc>.*)$/i;
const CATEGORY_LINE = /^\s*(?:[#>*\-]+\s*)*(?:(?<num>\d{1,3})[.)]\s*)?(?:\|\s*)?(?<name>[A-Z][A-Za-z0-9 ,/&'()\-]{3,90})\s*$/;
const NAME_DESC = /^(?<name>[^:]{3,90}?)\s*[:\-]\s*(?<desc>[A-Z].{15,})$/;
const HINT = /\b(skill|capabilit|tool|feature|operations?|analysis|design|automation)\b/i;

function looksLikeCategory(line) {
  const text = line.replace(/^\s*(?:[#>*-]+\s*)*(?:\d{1,3}[.)]\s*)?/, '').trim().replace(/[ .:-]+$/, '');
  if (text.length < 4 || text.length > 90) return false;
  const words = text.split(/\s+/);
  if (words.length < 2 || words.length > 12) return false;
  if (!/^[A-Z]/.test(text)) return false;
  if (HINT.test(text)) return true;
  return words.filter((w) => /^[A-Za-z]/.test(w)).every((w) => /^[A-Z]/.test(w) || STOPWORDS.has(w.toLowerCase()));
}

function extract(raw) {
  if (Buffer.byteLength(raw, 'utf8') > MAX_INPUT_BYTES) {
    throw new ToolError('INPUT_TOO_LARGE', `Input exceeds ${MAX_INPUT_BYTES} bytes.`, 'Split the source or raise the limit.');
  }
  const cleaned = /<(p|div|li|span|td|br|h[1-6])\b/i.test(raw) ? stripHtml(raw) : raw;
  const lines = cleaned
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(/\t/g, ' ').replace(/^[-*+]\s+(?=\d{1,3}[.)])/, '').replace(/\s{2,}/g, ' ').trim())
    .filter(Boolean);

  if (lines.length === 0) {
    throw new ToolError('INPUT_EMPTY', 'No usable text found in the input.', 'Paste plain text, Markdown or HTML.');
  }

  const categories = [];
  let current = null;
  let target = null;
  const warnings = [];

  const openCategory = (name, num) => {
    const category = {
      number: num ?? categories.length + 1,
      name: name.trim().replace(/[ .:-]+$/, ''),
      description: '',
      skills: [],
      _desc: [],
    };
    categories.push(category);
    return category;
  };

  for (const line of lines) {
    let skillMatch = SKILL_LINE.exec(line);
    if (!skillMatch) skillMatch = LABELLED_SKILL.exec(line);
    if (skillMatch) {
      if (!current) current = openCategory('extracted-capabilities', null);
      const skill = {
        number: current.skills.length + 1,
        name: skillMatch.groups.name.replace(/[.]\s*$/, '').trim(),
        description: '',
        _desc: [],
      };
      current.skills.push(skill);
      target = skill;
      continue;
    }
    const descMatch = DESC_LINE.exec(line);
    if (descMatch && target) {
      target._desc.push(descMatch.groups.desc.trim());
      continue;
    }
    const catMatch = CATEGORY_LINE.exec(line);
    if (catMatch && looksLikeCategory(line)) {
      current = openCategory(catMatch.groups.name, catMatch.groups.num ? Number(catMatch.groups.num) : null);
      target = current;
      continue;
    }
    const pair = NAME_DESC.exec(line);
    if (pair && current) {
      const skill = { number: current.skills.length + 1, name: pair.groups.name.trim().replace(/[ .:-]+$/, ''), description: pair.groups.desc.trim(), _desc: [] };
      current.skills.push(skill);
      target = skill;
      continue;
    }
    if (target) target._desc.push(line);
  }

  for (const category of categories) {
    category.description = category._desc.join(' ').replace(/\s+/g, ' ').trim();
    delete category._desc;
    category.slug = slugify(category.name);
    category.skills.forEach((skill, index) => {
      skill.number = index + 1;
      skill.id = `${category.number}.${index + 1}`;
    });
    for (const skill of category.skills) {
      skill.description = skill._desc.join(' ').replace(/\s+/g, ' ').trim();
      delete skill._desc;
      if (!skill.description) warnings.push(`skill '${skill.name}' had no description; recorded as action-only capability`);
      skill.slug = slugify(skill.name);
      skill.keywords = [...new Set((`${skill.name} ${skill.description}`.toLowerCase().match(/[a-z][a-z0-9+/#]{2,}/g) ?? []))]
        .filter((w) => !STOPWORDS.has(w))
        .sort();
    }
  }

  const kept = categories.filter((c) => c.skills.length > 0);
  if (kept.length === 0) {
    throw new ToolError('EXTRACT_EMPTY', 'Could not find any capability entries.', "Use '1. Skill Name: Name' + 'Description: ...' or 'Name: description' lines.");
  }
  if (kept.length > MAX_CATEGORIES) {
    throw new ToolError('EXTRACT_TOO_LARGE', `${kept.length} categories exceeds the parser limit of ${MAX_CATEGORIES}.`);
  }
  for (const category of kept) {
    if (category.skills.length > MAX_SKILLS_PER_CATEGORY) {
      throw new ToolError('EXTRACT_TOO_LARGE', `category '${category.name}' has ${category.skills.length} skills, limit ${MAX_SKILLS_PER_CATEGORY}.`);
    }
  }
  const skillCount = kept.reduce((total, c) => total + c.skills.length, 0);
  return {
    catalog: 'extracted-capability-catalog',
    source: 'user-supplied capability list (node parser)',
    category_count: kept.length,
    skill_count: skillCount,
    extraction_warnings: warnings.slice(0, 40),
    categories: kept,
  };
}

function verifyCatalog(doc) {
  const problems = [];
  if (typeof doc !== 'object' || doc === null) problems.push('catalog is not an object');
  const categories = doc?.categories;
  if (!Array.isArray(categories) || categories.length === 0) problems.push('categories must be a non-empty array');
  else {
    if (categories.length > MAX_CATEGORIES) problems.push(`${categories.length} categories exceeds limit ${MAX_CATEGORIES}`);
    const seen = new Set();
    let total = 0;
    for (const category of categories) {
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(String(category?.slug ?? ''))) problems.push(`bad category slug: ${category?.slug}`);
      if (!Array.isArray(category?.skills) || category.skills.length === 0) problems.push(`category ${category?.name} has no skills`);
      else if (category.skills.length > MAX_SKILLS_PER_CATEGORY) problems.push(`category ${category.name} exceeds ${MAX_SKILLS_PER_CATEGORY} skills`);
      for (const skill of category?.skills ?? []) {
        total += 1;
        if (!/^\d{1,3}\.\d{1,3}$/.test(String(skill?.id ?? ''))) problems.push(`bad skill id: ${skill?.id}`);
        if (seen.has(skill?.id)) problems.push(`duplicate skill id: ${skill?.id}`);
        seen.add(skill?.id);
        if (!skill?.name) problems.push(`skill ${skill?.id} missing name`);
        if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(String(skill?.slug ?? ''))) problems.push(`bad skill slug: ${skill?.slug}`);
        if (!skill?.description) problems.push(`skill ${skill?.id} missing description`);
      }
    }
    if (typeof doc?.skill_count === 'number' && doc.skill_count !== total) {
      problems.push(`declared skill_count ${doc.skill_count} != actual ${total}`);
    }
  }
  return { problems, skillCount: doc?.skill_count ?? null, categoryCount: doc?.category_count ?? null };
}

function parseArgs(argv) {
  const opts = { format: 'text', command: null, input: null, out: null, catalog: null };
  const rest = [...argv];
  const command = rest.shift();
  if (command && !command.startsWith('-')) opts.command = command;
  else if (command) rest.unshift(command);
  for (let i = 0; i < rest.length; i += 1) {
    const arg = rest[i];
    if (arg === '--help' || arg === '-h') opts.help = true;
    else if (arg === '--version') opts.version = true;
    else if (arg === '--format') opts.format = rest[++i] ?? 'text';
    else if (arg === '--input') opts.input = rest[++i] ?? null;
    else if (arg === '--out') opts.out = rest[++i] ?? null;
    else if (arg === '--catalog') opts.catalog = rest[++i] ?? null;
    else if (arg === '--debug') opts.debug = true;
    else throw new ToolError('ARG_UNKNOWN', `Unknown argument: ${arg}`, 'Run with --help.');
  }
  if (opts.format !== 'text' && opts.format !== 'json') throw new ToolError('ARG_INVALID', "--format must be 'text' or 'json'.");
  return opts;
}

const HELP = `${TOOL} ${VERSION} - zero-dependency capability-list parser (companion to tools-spreadsheet-skill-library.py)

Usage:
  node tools-catalog-parser.mjs <operation> [options]

Operations:
  extract       Parse a text/Markdown/HTML capability list into catalog JSON
  verify        Validate a catalog JSON file
  capabilities  List operations

Options:
  --input PATH     source file for extract (required)
  --out PATH       output .json for extract (default: extracted-catalog.json)
  --catalog PATH   catalog JSON for verify (default: asset-skill-catalog.json)
  --format text|json  output format (default: text)
  --debug          print a stack trace on unexpected failures
  --help, --version

Exit codes: 0 ok, 2 invalid input, 4 internal error.`;

function emit(opts, command, data, text) {
  if (opts.format === 'json') {
    process.stdout.write(`${JSON.stringify({ ok: true, tool: 'sheets-skill-library', version: VERSION, parser: TOOL, command, data }, null, 2)}\n`);
  } else if (text) {
    process.stdout.write(`${text}\n`);
  }
}

function atomicWrite(path, contents) {
  const tmp = join(dirname(path), `.${path.split('/').pop()}.tmp-${process.pid}`);
  writeFileSync(tmp, contents, 'utf8');
  renameSync(tmp, path);
}

function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`${error.code}: ${error.message}\n`);
    return EXIT_INVALID;
  }
  if (opts.version) { process.stdout.write(`${TOOL} ${VERSION}\n`); return EXIT_OK; }
  if (opts.help || !opts.command) { process.stdout.write(`${HELP}\n`); return opts.help ? EXIT_OK : EXIT_INVALID; }

  try {
    if (opts.command === 'capabilities') {
      emit(opts, 'capabilities', { operations: CAPABILITIES.map(([operation, description]) => ({ operation, description })), parser: TOOL },
        CAPABILITIES.map(([operation, description]) => `  ${operation.padEnd(14)} ${description}`).join('\n'));
      return EXIT_OK;
    }
    if (opts.command === 'extract') {
      if (!opts.input) throw new ToolError('MISSING_INPUT', 'extract requires --input.', 'Pass a .txt, .md or .html capability list.');
      const sourcePath = resolve(opts.input);
      if (!existsSync(sourcePath)) throw new ToolError('FILE_NOT_FOUND', `Input not found: ${sourcePath}`);
      if (statSync(sourcePath).size > MAX_INPUT_BYTES) throw new ToolError('INPUT_TOO_LARGE', `${sourcePath} exceeds ${MAX_INPUT_BYTES} bytes.`);
      const doc = extract(readFileSync(sourcePath, 'utf8'));
      const outPath = resolve(opts.out ?? 'extracted-catalog.json');
      atomicWrite(outPath, `${JSON.stringify(doc, null, 2)}\n`);
      const summary = { output: outPath, category_count: doc.category_count, skill_count: doc.skill_count, warnings: doc.extraction_warnings };
      emit(opts, 'extract', summary, `Extracted ${doc.skill_count} capabilities in ${doc.category_count} categories -> ${outPath}`);
      return EXIT_OK;
    }
    if (opts.command === 'verify') {
      const catalogPath = resolve(opts.catalog ?? join(SCRIPT_DIR, 'asset-skill-catalog.json'));
      if (!existsSync(catalogPath)) throw new ToolError('FILE_NOT_FOUND', `Catalog not found: ${catalogPath}`);
      const doc = JSON.parse(readFileSync(catalogPath, 'utf8'));
      const { problems, skillCount, categoryCount } = verifyCatalog(doc);
      const payload = { catalog: catalogPath, category_count: categoryCount, skill_count: skillCount, problems };
      if (problems.length) {
        throw new ToolError('CATALOG_INVALID', `${problems.length} problem(s): ${problems.slice(0, 5).join('; ')}`, 'Fix the flagged entries and re-run verify.', EXIT_INVALID);
      }
      emit(opts, 'verify', payload, `Catalog OK: ${categoryCount} categories / ${skillCount} capabilities (${catalogPath})`);
      return EXIT_OK;
    }
    throw new ToolError('COMMAND_UNKNOWN', `Unknown operation '${opts.command}'.`, 'Run with --help.');
  } catch (error) {
    if (error instanceof ToolError) {
      if (opts.format === 'json') process.stdout.write(`${JSON.stringify(error.toJSON(), null, 2)}\n`);
      else process.stderr.write(`${error.code}: ${error.message}${error.hint ? ` (${error.hint})` : ''}\n`);
      return error.exitCode;
    }
    if (opts.debug) process.stderr.write(`${error?.stack ?? error}\n`);
    if (opts.format === 'json') {
      process.stdout.write(`${JSON.stringify({ ok: false, error: { code: 'INTERNAL', message: String(error?.message ?? error) } }, null, 2)}\n`);
    } else {
      process.stderr.write(`INTERNAL: ${error?.message ?? error}\n`);
    }
    return EXIT_INTERNAL;
  }
}

process.exit(main());
