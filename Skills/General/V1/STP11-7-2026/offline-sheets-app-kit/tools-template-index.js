/* =====================================================================
   offline-sheets-app-kit - tools-template-index.js
   ---------------------------------------------------------------------
   Reports what this kit contains and how each flat template maps onto a
   generated project. The kit keeps no subfolders, so a template's destination
   path is encoded in its filename:

       asset-tpl-app__js__pos.js   ->   <project>/app/js/pos.js

   Usage:
       node tools-template-index.js --json      (default) machine-readable map
       node tools-template-index.js --list      one destination per line
       node tools-template-index.js --placeholders
       node tools-template-index.js --help
   ===================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const PREFIX = 'asset-tpl-';
const TOKEN = /\{\{([A-Z0-9_]+)\}\}/g;
const TEXT_EXT = ['.js', '.css', '.html', '.gs', '.json', '.txt', '.xml', '.md', '.sh'];

function index() {
  const files = fs.readdirSync(HERE).filter(function (n) {
    return n.startsWith(PREFIX) && fs.statSync(path.join(HERE, n)).isFile();
  });

  const destinations = {};
  const placeholders = new Set();

  files.forEach(function (name) {
    const dest = name.slice(PREFIX.length).split('__').join('/');
    destinations[dest] = name;
    if (TEXT_EXT.indexOf(path.extname(name).toLowerCase()) < 0) return;
    const text = fs.readFileSync(path.join(HERE, name), 'utf8');
    let m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text)) !== null) placeholders.add(m[1]);
  });

  return {
    ok: files.length > 0,
    kitDir: path.basename(HERE),
    templateCount: files.length,
    destinations: Object.keys(destinations).sort(),
    map: destinations,
    placeholders: Array.from(placeholders).sort(),
    error: files.length ? undefined : 'no ' + PREFIX + '* files found beside this tool'
  };
}

if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    console.log([
      'Usage: node tools-template-index.js [--json] [--list] [--placeholders]',
      '',
      '  --json          full map of template file -> generated path (default)',
      '  --list          one generated path per line',
      '  --placeholders  every {{TOKEN}} the templates use',
      '',
      'Exposes the kit contents to the generator and to anyone auditing it.'
    ].join('\n'));
    process.exit(0);
  }
  const data = index();
  if (!data.ok) { console.error(JSON.stringify(data)); process.exit(2); }
  if (args.includes('--list')) { console.log(data.destinations.join('\n')); process.exit(0); }
  if (args.includes('--placeholders')) { console.log(data.placeholders.join('\n')); process.exit(0); }
  console.log(JSON.stringify(data, null, 2));
  process.exit(0);
}

module.exports = { index };
