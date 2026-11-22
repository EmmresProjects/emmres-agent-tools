#!/usr/bin/env node
/**
 * Copies ../../app  ->  ./www  so Cordova always builds the same code
 * that lives in one place. Run before every `cordova build`.
 *
 *   node scripts/sync-www.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

const root = __dirname + '/..';
const src = path.resolve(root, '..', 'app');
const dest = path.resolve(root, 'www');

function rm(dir) {
  if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
}
function copy(srcDir, destDir) {
  fs.mkdirSync(destDir, { recursive: true });
  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    const s = path.join(srcDir, entry.name);
    const d = path.join(destDir, entry.name);
    if (entry.isDirectory()) copy(s, d);
    else fs.copyFileSync(s, d);
  }
}

if (!fs.existsSync(src)) {
  console.error('Cannot find the app folder at ' + src);
  process.exit(1);
}

rm(dest);
copy(src, dest);

// The mock seed file is a dev-only artefact
const mock = path.join(dest, 'js', 'seed.js.mock.json');
if (fs.existsSync(mock)) fs.unlinkSync(mock);

// The single-file build is only for sharing/preview, never packaged
const single = path.join(dest, '{{ARTIFACT_SLUG}}.html');
if (fs.existsSync(single)) fs.unlinkSync(single);

const count = (function walk(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).reduce((n, e) =>
    n + (e.isDirectory() ? walk(path.join(dir, e.name)) : 1), 0);
})(dest);

console.log('[sync-www] copied app/ -> cordova/www/ (' + count + ' files)');
