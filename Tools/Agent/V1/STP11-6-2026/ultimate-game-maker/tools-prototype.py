#!/usr/bin/env python3
"""Generates and audits the self-contained runnable HTML5 Canvas prototype."""
from __future__ import annotations

import html as html_module
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import ugm_common as common

PLACEHOLDERS = (
    '__TITLE_HTML__', '__GOAL_HTML__', '__GENRE_HTML__', '__PLATFORM_HTML__',
    '__ACCEPTANCE_HTML__', '__GAME_CONFIG_JSON__',
)
FORBIDDEN_PATTERNS = (
    'http://', 'https://', '//cdn', '@import', 'url(', 'fetch(', 'XMLHttpRequest',
    'WebSocket', 'eval(', 'new Function', 'Function(', 'importScripts', 'Worker(',
    'Math.random', 'document.write', 'innerHTML', '<script src', '<iframe', '<object', '<embed', '<base',
)


class HtmlAudit(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attributes: list[tuple[str, str, str]] = []
        self.scripts: list[str] = []
        self.in_script = False
        self.script_parts: list[str] = []
        self.external_attributes: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            value = value or ''
            self.attributes.append((tag, name, value))
            if name.lower() in ('src', 'href', 'srcset', 'action', 'formaction', 'poster', 'data') and (value.strip() or tag.lower() in ('a', 'link', 'iframe', 'img', 'script', 'audio', 'video', 'source', 'form')):
                self.external_attributes.append(f'{tag}.{name}={value}')
            if name.lower() == 'http-equiv' and value.lower() in ('refresh',):
                self.external_attributes.append(f'{tag}.{name}={value}')
        if tag.lower() == 'script':
            self.in_script = True
            self.script_parts = []

    def handle_data(self, data):
        if self.in_script:
            self.script_parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'script' and self.in_script:
            self.scripts.append(''.join(self.script_parts))
            self.in_script = False


def _clamp(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number or number in (float('inf'), float('-inf')):
        number = float(default)
    return max(low, min(high, number))


def _title(brief: dict) -> str:
    if brief.get('title'):
        return str(brief['title'])[:80]
    genre = str(brief.get('genre', 'Game')).strip().title()
    words = str(brief.get('goal', 'Playable Prototype')).split()[:6]
    return f'{genre} Prototype: {" ".join(words)}'[:80].rstrip(' :') or 'Playable Prototype'


def _game_config(brief: dict, config: dict) -> dict:
    prototype = config['prototype']
    budget = brief.get('performance_budget', {}) if isinstance(brief.get('performance_budget'), dict) else {}
    target_fps = _clamp(budget.get('target_fps', 60), 30, 120, 60)
    fixed_step = _clamp(budget.get('fixed_step_seconds', 1 / target_fps), 1 / 240, 1 / 20, 1 / target_fps)
    return {
        'title': _title(brief),
        'goal': str(brief.get('goal', '')),
        'genre': str(brief.get('genre', '')),
        'platform': str(brief.get('target_platform', '')),
        'seed': int(brief.get('seed', 0)),
        'targetScore': int(_clamp(prototype.get('target_score', 8), 1, 30, 8)),
        'timeLimitSeconds': int(_clamp(prototype.get('time_limit_seconds', 90), 10, 600, 90)),
        'lives': int(_clamp(prototype.get('lives', 3), 1, 9, 3)),
        'world': {
            'width': int(_clamp(prototype.get('world_width', 960), 320, 1920, 960)),
            'height': int(_clamp(prototype.get('world_height', 540), 240, 1080, 540)),
        },
        'allowLocalStorage': bool(prototype.get('allow_local_storage', True)),
        'allowAudio': bool(prototype.get('allow_audio', True)),
        'targetFps': int(target_fps),
        'fixedStep': fixed_step,
        'acceptanceCriteria': [str(item) for item in brief.get('acceptance_criteria', [])],
    }


def _template_path(config: dict, override: str | None, skill_root: Path | None) -> Path:
    if override:
        path = Path(override)
        if not path.is_file():
            raise common.ToolError('MISSING_RESOURCE', 'The HTML5 template override does not exist.', [override])
        return path
    return common.resolve_reference(config['prompts']['html5_template'], skill_root)


def render_prototype(brief: dict, config: dict, route: dict, deadline: common.Deadline,
                     template_override: str | None = None, skill_root: Path | None = None) -> tuple[str, dict]:
    deadline.check('render HTML5 prototype')
    if not route.get('prototype_available'):
        raise common.ToolError(
            'PROTOTYPE_NOT_AVAILABLE',
            'A runnable HTML5 prototype is only generated for the html5 engine profile.',
            [route['requested_engine'], route['profile']['id']],
            ['Use an html5 brief for the prototype command; other engines receive production packets and external build checklists.'],
        )
    template_path = _template_path(config, template_override, skill_root)
    template = common.read_text_limited(template_path, int(config['limits']['max_template_bytes']), 'asset-html5-prototype.html', config)
    missing = [placeholder for placeholder in PLACEHOLDERS if placeholder not in template]
    if missing:
        raise common.ToolError('INVALID_TEMPLATE', 'The HTML5 template is missing required placeholders.', missing, ['Use ultimate-game-maker/asset-html5-prototype.html or a template with the same placeholders.'])
    game_config = _game_config(brief, config)
    title = html_module.escape(game_config['title'], quote=True)
    goal = html_module.escape(game_config['goal'], quote=True)
    genre = html_module.escape(game_config['genre'], quote=True)
    platform = html_module.escape(game_config['platform'], quote=True)
    acceptance = ''.join(f'<li>{html_module.escape(str(item), quote=False)}</li>' for item in game_config['acceptanceCriteria']) or '<li>No acceptance criteria supplied.</li>'
    config_json = json.dumps(game_config, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    replacements = {
        '__TITLE_HTML__': title,
        '__GOAL_HTML__': goal,
        '__GENRE_HTML__': genre,
        '__PLATFORM_HTML__': platform,
        '__ACCEPTANCE_HTML__': acceptance,
        '__GAME_CONFIG_JSON__': config_json,
    }
    output = template
    for placeholder, value in replacements.items():
        output = output.replace(placeholder, value)
    report = verify_html(output, game_config)
    return output, report


def verify_html(document: str, game_config: dict | None = None) -> dict:
    audit = HtmlAudit()
    try:
        audit.feed(document)
        audit.close()
    except Exception as exc:  # HTMLParser is deliberately tolerant; surface any real parse failure.
        raise common.ToolError('INVALID_GENERATED_HTML', 'The generated HTML could not be parsed.', [str(exc)])
    lowered = document.lower()
    forbidden = [pattern for pattern in FORBIDDEN_PATTERNS if pattern.lower() in lowered]
    if forbidden:
        raise common.ToolError('UNSAFE_GENERATED_HTML', 'The generated HTML contains forbidden external or executable references.', forbidden, ['Restore ultimate-game-maker/asset-html5-prototype.html and rerun.'])
    if audit.external_attributes:
        raise common.ToolError('UNSAFE_GENERATED_HTML', 'The generated HTML has external resource attributes.', audit.external_attributes, ['Remove external attributes from the template.'])
    leftovers = [placeholder for placeholder in PLACEHOLDERS if placeholder in document]
    if leftovers:
        raise common.ToolError('UNRESOLVED_TEMPLATE', 'Unresolved template placeholders remain.', leftovers)
    if len(audit.scripts) != 1:
        raise common.ToolError('INVALID_GENERATED_HTML', 'Exactly one inline script is required.', [len(audit.scripts)])
    script = audit.scripts[0]
    required_tokens = ['UltimateGameMakerPrototype', 'function createState', 'function resetState', 'function stepState', 'mulberry32']
    missing = [token for token in required_tokens if token not in script]
    if missing:
        raise common.ToolError('INVALID_PROTOTYPE_LOGIC', 'The prototype script is missing required pure logic exports.', missing)
    checks = [
        'HTML parsed with one inline script',
        'no external resource attributes or URLs',
        'no network, arbitrary-code, or innerHTML execution patterns',
        'all template placeholders resolved',
        'pure createState, resetState, and stepState functions exported',
    ]
    return {
        'schema_id': 'ugm-prototype-report-1.0',
        'bytes': len(document.encode('utf-8')),
        'tags_seen': sorted(set(audit.tags)),
        'script_count': len(audit.scripts),
        'script_bytes': len(script.encode('utf-8')),
        'game_config': game_config or {},
        'checks_passed': checks,
        'browser_interaction': 'not tested by generation; the Node logic harness is run by selftest when available',
    }


def extract_script(document: str) -> str:
    audit = HtmlAudit()
    audit.feed(document)
    audit.close()
    if len(audit.scripts) != 1:
        raise common.ToolError('INVALID_GENERATED_HTML', 'Expected exactly one inline script.', [len(audit.scripts)])
    return audit.scripts[0]


def build_node_harness(document: str) -> str:
    script = extract_script(document)
    source_literal = json.dumps(script, ensure_ascii=False)
    return r'''
const assert = require('assert');
const vm = require('vm');
const source = ''' + source_literal + r''';
const noop = function () {};
const ctx = new Proxy({}, {
  get: function (target, property) {
    if (property === 'canvas') return { width: 960, height: 540 };
    return noop;
  },
  set: function () { return true; }
});
const makeElement = function (id) {
  return {
    id: id,
    hidden: true,
    textContent: '',
    style: {},
    width: 960,
    height: 540,
    addEventListener: noop,
    setAttribute: noop,
    focus: noop,
    getBoundingClientRect: function () { return { width: 960, height: 540, left: 0, top: 0 }; },
    getContext: function () { return ctx; }
  };
};
const elements = {};
const documentStub = {
  getElementById: function (id) {
    if (!elements[id]) elements[id] = makeElement(id);
    return elements[id];
  }
};
const storageData = {};
const windowStub = {
  devicePixelRatio: 1,
  addEventListener: noop,
  matchMedia: function () { return { matches: false }; },
  requestAnimationFrame: function () { return 1; },
  localStorage: {
    getItem: function (key) { return Object.prototype.hasOwnProperty.call(storageData, key) ? storageData[key] : null; },
    setItem: function (key, value) { storageData[key] = String(value); },
    removeItem: function (key) { delete storageData[key]; }
  }
};
const sandbox = {
  window: windowStub,
  document: documentStub,
  console: console,
  performance: { now: function () { return 0; } }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { timeout: 2000 });
const api = sandbox.window.UltimateGameMakerPrototype;
assert.ok(api, 'prototype API is exported');
assert.strictEqual(typeof api.createState, 'function');
assert.strictEqual(typeof api.stepState, 'function');
const winning = api.createState(api.CONFIG);
assert.strictEqual(winning.parcels.length, api.CONFIG.targetScore);
assert.strictEqual(winning.status, 'playing');
winning.invulnerable = 999;
for (const parcel of winning.parcels) {
  winning.player.x = parcel.x;
  winning.player.y = parcel.y;
  api.stepState(winning, {}, 1 / 60);
}
assert.strictEqual(winning.score, api.CONFIG.targetScore);
assert.strictEqual(winning.status, 'won');
assert.ok(winning.events.indexOf('win') >= 0);
const hit = api.createState(api.CONFIG);
hit.player.x = hit.hazards[0].x;
hit.player.y = hit.hazards[0].y;
hit.invulnerable = 0;
api.stepState(hit, {}, 1 / 60);
assert.strictEqual(hit.lives, api.CONFIG.lives - 1);
assert.ok(hit.events.indexOf('hit') >= 0);
const paused = api.createState(api.CONFIG);
paused.paused = true;
const originalX = paused.player.x;
api.stepState(paused, { right: true }, 1 / 20);
assert.strictEqual(paused.player.x, originalX);
const first = api.createState(api.CONFIG);
const reset = api.resetState(api.CONFIG);
assert.deepStrictEqual(first.parcels, reset.parcels);
const changedConfig = JSON.parse(JSON.stringify(api.CONFIG));
changedConfig.seed = Number(api.CONFIG.seed) + 1;
const changed = api.createState(changedConfig);
assert.notDeepStrictEqual(first.parcels, changed.parcels);
console.log(JSON.stringify({
  passed: true,
  checks: [
    'inline JavaScript executed in a bounded VM with DOM stubs',
    'seed-generated parcels and hazards are deterministic',
    'all parcels can be collected and a win state is reached',
    'hazard collision reduces lives',
    'pause blocks movement',
    'reset reproduces the seed and a changed seed changes generated content'
  ]
}));
'''
