#!/usr/bin/env python3
"""
offline-sheets-app-kit - tools-generate-kit.py
==============================================
Turn one declarative JSON specification into a complete, runnable business app:

    offline-first web app  +  Google Apps Script backend  +  Google Sheets
    workbook  +  Android (Cordova) build  +  Windows (Electron) build  +  docs

The generated project keeps working with the internet unplugged, syncs
records into a spreadsheet when it comes back, and ships with every setup
guide and build command already written.

QUICK START
-----------
    python tools-generate-kit.py list                       # bundled specs
    python tools-generate-kit.py validate --spec asset-example-spec-cafe.json
    python tools-generate-kit.py new --spec asset-example-spec-cafe.json \
        --out ./out/brew-street
    python tools-generate-kit.py build-single-file --project ./out/brew-street
    python tools-generate-kit.py help --long

Exit codes: 0 ok | 1 runtime failure | 2 bad arguments | 3 missing dependency
            4 validation failed | 5 refused (approval gate) | 6 timeout

Dependencies: Python 3.9+, Node.js 18+ (for the JS syntax check),
              openpyxl and Pillow (pip install openpyxl pillow).
"""
import argparse
import hashlib
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, 'asset-spec-schema.json')
# Templates are flat files named asset-tpl-<path>__<to>__<file>. The kit keeps no
# subfolders, so each template's destination path is encoded in its own filename:
#   asset-tpl-app__js__pos.js   ->   <project>/app/js/pos.js
TEMPLATE_PREFIX = 'asset-tpl-'

VERSION = '1.0.0'
GENERATOR = 'offline-sheets-app-kit'

EXIT_OK, EXIT_FAIL, EXIT_ARGS, EXIT_DEP, EXIT_VALID, EXIT_GATE, EXIT_TIMEOUT = 0, 1, 2, 3, 4, 5, 6

# ------------------------------------------------------------------ limits
LIMITS = {
    'max_products': 500,
    'max_accounts': 100,
    'max_branches': 20,
    'max_roles': 10,
    'max_payment_methods': 6,
    'max_non_revenue_types': 8,
    'max_spec_bytes': 2 * 1024 * 1024,
    'out_path_max_len': 220,
}
TIMEOUTS = {'node_check': 60, 'excel': 180, 'icons': 180, 'single_file': 120}
RETRIES = {'node_check': 1, 'excel': 1, 'icons': 1}


# ------------------------------------------------------------------ errors
class KitError(Exception):
    """A structured, user-facing failure. Never dumps a raw traceback at users."""

    def __init__(self, code, message, hint=None, details=None, exit_code=EXIT_FAIL):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details or []
        self.exit_code = exit_code

    def as_dict(self):
        out = {'ok': False, 'error': {'code': self.code, 'message': self.message}}
        if self.hint:
            out['error']['hint'] = self.hint
        if self.details:
            out['error']['details'] = self.details
        return out


def fail(code, message, hint=None, details=None, exit_code=EXIT_FAIL):
    raise KitError(code, message, hint, details, exit_code)


# ------------------------------------------------------------------ helpers
def slugify(text, sep='-'):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode()
    text = re.sub(r'[^a-zA-Z0-9]+', sep, text).strip(sep).lower()
    return text or 'app'


def title_upper(text):
    return str(text).upper()


def rand_token():
    alphabet = string.ascii_letters + string.digits
    return 'CHANGE-ME-' + ''.join(random.SystemRandom().choice(alphabet) for _ in range(28))


def money(value):
    v = float(value or 0)
    return f'{v:,.2f}'


def load_json(path):
    if not os.path.isfile(path):
        fail('SPEC_NOT_FOUND', f'No specification at {path}',
             'Run "list" to see the bundled examples, or pass --spec <file.json>.',
             exit_code=EXIT_ARGS)
    try:
        size = os.path.getsize(path)
    except OSError as e:
        fail('SPEC_UNREADABLE', f'Could not read {path}: {e.strerror}', exit_code=EXIT_ARGS)
    if size > LIMITS['max_spec_bytes']:
        fail('SPEC_TOO_LARGE',
             f'Specification is {size} bytes; the limit is {LIMITS["max_spec_bytes"]}.',
             'Split the catalogue into a second deployment instead of one huge file.')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        fail('SPEC_NOT_FOUND', f'No specification at {path}',
             'Run "list" to see the bundled examples, or pass --spec <file.json>.', exit_code=EXIT_ARGS)
    except json.JSONDecodeError as e:
        fail('SPEC_NOT_JSON', f'Specification is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}.',
             'Check for a trailing comma or a missing quote.', exit_code=EXIT_ARGS)


# ------------------------------------------------------------------ validation
PERMISSIONS = {'pos.use', 'kitchen.view', 'kitchen.update', 'payment.collect', 'order.void.own',
               'order.void.any', 'reports.view', 'nonrev.log', 'eod.manage', 'products.manage',
               'export'}
BADGE_VALUES = {'warn', 'info', 'bad', 'good'}


def _require(obj, key, where, errors):
    if key not in obj or obj[key] in (None, ''):
        errors.append(f'{where}: missing required field "{key}"')
        return False
    return True


def validate_spec(spec, known_template_placeholders=None):
    """Return (errors, warnings). Errors block generation."""
    errors, warnings = [], []

    if not isinstance(spec, dict):
        return ['spec: the top level must be a JSON object'], []

    for key in ('app', 'accounts', 'roles', 'branches', 'products', 'paymentMethods',
                'nonRevenueTypes', 'settings'):
        _require(spec, key, 'spec', errors)
    if errors:
        return errors, warnings

    # ---------------- app
    app = spec['app']
    for key in ('name', 'title', 'slug', 'businessName'):
        _require(app, key, 'app', errors)
    if app.get('slug') and not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', app['slug']):
        errors.append('app.slug: use lowercase letters, digits and single hyphens (e.g. "brew-street")')
    if app.get('androidAppId') and not re.match(r'^[a-z][a-z0-9]*(\.[a-z0-9]+)+$', app['androidAppId']):
        errors.append('app.androidAppId: must be a reverse-domain id such as ph.brewstreet.pos')
    for colour in ('brandColor', 'brandColorAlt', 'brandInk', 'themeColor'):
        if colour in app and not re.match(r'^#[0-9a-fA-F]{6}$', app[colour]):
            errors.append(f'app.{colour}: must be a 6-digit hex colour such as #ffb02e')
    version = app.get('version', '1.0.0')
    if not re.match(r'^\d+\.\d+\.\d+$', version):
        errors.append('app.version: use MAJOR.MINOR.PATCH, e.g. 1.0.0')
    if app.get('orientation') and app['orientation'] not in ('default', 'portrait', 'landscape'):
        errors.append('app.orientation: must be default, portrait or landscape')

    # ---------------- branches
    branch_ids = set()
    if len(spec['branches']) > LIMITS['max_branches']:
        errors.append(f'branches: {len(spec["branches"])} given, limit is {LIMITS["max_branches"]}')
    for i, b in enumerate(spec['branches']):
        where = f'branches[{i}]'
        if not _require(b, 'id', where, errors) or not _require(b, 'name', where, errors):
            continue
        if not re.match(r'^[A-Z]{2,4}-\d{2}$', b['id']):
            errors.append(f'{where}.id: use two to four capitals, a hyphen and two digits (e.g. BR-01)')
        if b['id'] in branch_ids:
            errors.append(f'{where}.id: duplicate branch id {b["id"]}')
        branch_ids.add(b['id'])

    # ---------------- roles
    role_ids = set()
    if len(spec['roles']) > LIMITS['max_roles']:
        errors.append(f'roles: {len(spec["roles"])} given, limit is {LIMITS["max_roles"]}')
    for i, r in enumerate(spec['roles']):
        where = f'roles[{i}]'
        if not _require(r, 'id', where, errors) or not _require(r, 'name', where, errors):
            continue
        if not re.match(r'^ROLE-[A-Z0-9-]+$', r['id']):
            errors.append(f'{where}.id: must look like ROLE-ADMIN')
        if r['id'] in role_ids:
            errors.append(f'{where}.id: duplicate role id {r["id"]}')
        role_ids.add(r['id'])
        perms = r.get('permissions') or []
        if not perms:
            errors.append(f'{where}.permissions: give at least one permission')
        for p in perms:
            if p not in PERMISSIONS:
                errors.append(f'{where}.permissions: unknown permission "{p}". '
                              f'Allowed: {", ".join(sorted(PERMISSIONS))}')
    if not any(r.get('isAdmin') for r in spec['roles']):
        errors.append('roles: at least one role must have "isAdmin": true, '
                      'otherwise nobody can manage accounts')

    # ---------------- accounts
    acc = spec['accounts']
    if 'list' not in acc or not isinstance(acc.get('list'), list):
        errors.append('accounts.list: must be an array of account objects')
        return errors, warnings
    if len(acc['list']) > LIMITS['max_accounts']:
        errors.append(f'accounts.list: {len(acc["list"])} given, limit is {LIMITS["max_accounts"]}')
    usernames = set()
    account_ids = set()
    for i, a in enumerate(acc['list']):
        where = f'accounts.list[{i}]'
        for key in ('accountId', 'username', 'displayName', 'roleId', 'branchId', 'password'):
            _require(a, key, where, errors)
        if a.get('username'):
            if not re.match(r'^[a-zA-Z0-9._-]{3,32}$', a['username']):
                errors.append(f'{where}.username: 3-32 characters, letters/digits/._- only')
            if a['username'].lower() in usernames:
                errors.append(f'{where}.username: duplicate username "{a["username"]}"')
            usernames.add(a['username'].lower())
        if a.get('accountId'):
            if a['accountId'] in account_ids:
                errors.append(f'{where}.accountId: duplicate account id {a["accountId"]}')
            account_ids.add(a['accountId'])
        if a.get('roleId') and a['roleId'] not in role_ids:
            errors.append(f'{where}.roleId: "{a["roleId"]}" is not declared in roles[]')
        if a.get('branchId') and a['branchId'] not in branch_ids:
            errors.append(f'{where}.branchId: "{a["branchId"]}" is not declared in branches[]')
        if a.get('password') and len(a['password']) < 8:
            errors.append(f'{where}.password: use at least 8 characters')
        if a.get('pin') and not re.match(r'^\d{4,8}$', a['pin']):
            errors.append(f'{where}.pin: 4 to 8 digits')
    if not usernames:
        errors.append('accounts.list: at least one account is required')
    owner_user = acc.get('ownerUsername')
    if owner_user and owner_user.lower() not in usernames:
        errors.append(f'accounts.ownerUsername: "{owner_user}" is not in accounts.list')
    owner_rows = [a for a in acc['list'] if a.get('username', '').lower() == (owner_user or '').lower()]
    if owner_rows and not any((r.get('isAdmin') for r in spec['roles'] if r['id'] == owner_rows[0].get('roleId'))):
        errors.append('accounts.ownerUsername: the owner account must use a role with "isAdmin": true')
    if acc.get('ownerPassword') and len(acc['ownerPassword']) < 8:
        errors.append('accounts.ownerPassword: use at least 8 characters')

    # ---------------- products
    category_ids = {c.get('id') for c in spec.get('categories', []) if c.get('id')}
    product_ids = set()
    if not spec['products']:
        errors.append('products: at least one product is required')
    if len(spec['products']) > LIMITS['max_products']:
        errors.append(f'products: {len(spec["products"])} given, limit is {LIMITS["max_products"]}')
    for i, p in enumerate(spec['products']):
        where = f'products[{i}]'
        if not _require(p, 'id', where, errors) or not _require(p, 'name', where, errors):
            continue
        if not re.match(r'^[A-Z0-9-]{2,16}$', p['id']):
            errors.append(f'{where}.id: 2-16 capitals, digits or hyphens (e.g. P-ESP-01)')
        if p['id'] in product_ids:
            errors.append(f'{where}.id: duplicate product id {p["id"]}')
        product_ids.add(p['id'])
        if not isinstance(p.get('price'), (int, float)) or p['price'] < 0:
            errors.append(f'{where}.price: must be a number, 0 or more')
        if p.get('category') and category_ids and p['category'] not in category_ids:
            warnings.append(f'{where}.category: "{p["category"]}" is not in categories[]; '
                            f'it will appear as its own group')
        for j, v in enumerate(p.get('volume') or []):
            if not v.get('unit'):
                errors.append(f'{where}.volume[{j}].unit: required')
            if not isinstance(v.get('qty'), (int, float)) or v['qty'] < 0:
                errors.append(f'{where}.volume[{j}].qty: must be a number, 0 or more')

    # ---------------- payment methods
    pm = spec['paymentMethods']
    if not 1 <= len(pm) <= LIMITS['max_payment_methods']:
        errors.append(f'paymentMethods: give 1 to {LIMITS["max_payment_methods"]} methods')
    seen = set()
    for i, m in enumerate(pm):
        if not isinstance(m, str) or not m.strip():
            errors.append(f'paymentMethods[{i}]: must be a non-empty string')
        elif m.strip().lower() in seen:
            errors.append(f'paymentMethods[{i}]: duplicate method "{m}"')
        else:
            seen.add(m.strip().lower())

    # ---------------- non-revenue types
    nrt = spec['nonRevenueTypes']
    if len(nrt) > LIMITS['max_non_revenue_types']:
        errors.append(f'nonRevenueTypes: {len(nrt)} given, limit is {LIMITS["max_non_revenue_types"]}')
    for i, t in enumerate(nrt):
        where = f'nonRevenueTypes[{i}]'
        if not _require(t, 'label', where, errors):
            continue
        if re.search(r'[:|]', t['label']):
            errors.append(f'{where}.label: may not contain ":" or "|" (they separate fields in the sheet)')
        if t.get('badge') and t['badge'] not in BADGE_VALUES:
            errors.append(f'{where}.badge: must be one of {", ".join(sorted(BADGE_VALUES))}')
        if t.get('hint') and re.search(r'[|]', t['hint']):
            errors.append(f'{where}.hint: may not contain "|"')

    # ---------------- queue + features
    q = spec.get('queue') or {}
    if 'min' in q and 'max' in q and q['min'] >= q['max']:
        errors.append('queue: min must be smaller than max')
    if spec.get('features', {}).get('queueCards') is False:
        warnings.append('features.queueCards is off: numbered queue cards will be hidden')
    if spec.get('features', {}).get('kitchen') is False:
        warnings.append('features.kitchen is off: the kitchen screen and queue strip will be hidden')

    # ---------------- settings values must be scalars
    for k, v in (spec.get('settings') or {}).items():
        if isinstance(v, (dict, list)):
            errors.append(f'settings.{k}: must be a string, number or true/false')

    # ---------------- template placeholder coverage (self-check)
    if known_template_placeholders:
        pass  # every placeholder is produced by the generator; nothing user-side

    return errors, warnings


# ------------------------------------------------------------------ spec -> runtime model
def normalise(spec):
    """Fill defaults and produce the single model every builder consumes."""
    app = spec['app']
    app.setdefault('version', '1.0.0')
    app.setdefault('tagline', '')
    app.setdefault('author', 'Operations')
    app.setdefault('authorEmail', 'ops@example.com')
    app.setdefault('brandColor', '#ffb02e')
    app.setdefault('brandColorAlt', '#ff8a1f')
    app.setdefault('brandInk', '#2b1b00')
    app.setdefault('themeColor', '#0b0f14')
    app.setdefault('androidAppId', 'ph.' + app['slug'].replace('-', '') + '.pos')
    app.setdefault('orientation', 'default')
    app.setdefault('keystoreAlias', 'release')
    app.setdefault('timeZone', 'Asia/Manila')
    app.setdefault('spreadsheetId', '')
    app.setdefault('initials', ''.join(w[0] for w in re.sub(r'[^A-Za-z0-9 ]', '', app['name']).split()[:2]).upper()
                   or app['name'][:1].upper())

    spec.setdefault('categories', [])
    for i, c in enumerate(spec['categories']):
        c.setdefault('id', f'CAT-{i + 1:02d}')
        c.setdefault('name', c['id'])
        c.setdefault('icon', '•')
    if not spec['categories']:
        cats = []
        for p in spec['products']:
            cid = p.get('category') or 'CAT-ALL'
            if cid not in [c['id'] for c in cats]:
                cats.append({'id': cid, 'name': cid.replace('CAT-', '').replace('-', ' ').title(), 'icon': '•'})
        spec['categories'] = cats

    features = spec.setdefault('features', {})
    for key, default in (('kitchen', True), ('queueCards', True), ('discounts', True),
                         ('productVolume', True), ('multiBranch', True), ('unpaidOrders', True)):
        features.setdefault(key, default)

    q = spec.setdefault('queue', {})
    q.setdefault('min', 1)
    q.setdefault('max', 18)

    spec.setdefault('token', {}).setdefault('default', '')
    if not spec['token']['default']:
        spec['token']['default'] = rand_token()

    settings = spec.setdefault('settings', {})
    required = {
        'BusinessName': app['businessName'],
        'Currency': '₱',
        'CurrencyCode': 'PHP',
        'QueueMin': str(q['min']),
        'QueueMax': str(q['max']),
        'AutoSync': 'Yes',
        'SyncIntervalSec': '45',
        'Theme': 'dark',
        'Density': 'comfortable',
        'AllowUnpaidOrders': 'Yes' if features['unpaidOrders'] else 'No',
        'RequireQueueNumber': 'Yes' if features['queueCards'] else 'No',
        'AutoReleaseQueue': 'Yes',
        'VoidWindowMinutes': '15',
        'KitchenWarnMinutes': '10',
        'ReceiptFooter': 'Thank you for your business!',
        'DeviceLabel': 'Counter 1',
        'PaymentMethods': '|'.join(spec['paymentMethods']),
        'NonRevenueTypes': '|'.join(
            f"{t['label']}:{t.get('badge', 'warn')}:{t.get('hint', '')}" for t in spec['nonRevenueTypes']),
        'FeatureKitchen': 'Yes' if features['kitchen'] else 'No',
        'FeatureQueueCards': 'Yes' if features['queueCards'] else 'No',
        'FeatureDiscounts': 'Yes' if features['discounts'] else 'No',
        'FeatureProductVolume': 'Yes' if features['productVolume'] else 'No',
        'FeatureMultiBranch': 'Yes' if features['multiBranch'] else 'No',
        'FeatureNonRevenue': 'Yes' if spec['nonRevenueTypes'] else 'No',
        'NonRevenuePageHint': 'Record stock that left without a sale. These entries never count as revenue.',
    }
    for k, v in required.items():
        settings.setdefault(k, v)

    notes = spec.setdefault('settingNotes', {})
    default_notes = {
        'BusinessName': 'Shown on the header, receipts and exports',
        'Currency': 'Symbol used everywhere money is displayed',
        'PaymentMethods': 'Checked-out buttons, in order. Separate with |',
        'NonRevenueTypes': 'Label:badge:hint entries. Badge = warn, info, bad or good',
        'QueueMin': 'Lowest numbered card',
        'QueueMax': 'Highest numbered card',
        'FeatureKitchen': 'No = hide the kitchen screen and queue strip',
        'FeatureQueueCards': 'No = no numbered cards; orders still save',
        'FeatureMultiBranch': 'No = hide the branch selector',
        'VoidWindowMinutes': 'How long a cashier may void their own order',
    }
    for k, v in default_notes.items():
        notes.setdefault(k, v)

    spec.setdefault('accounts', {})['list'] = [
        {**a, 'status': a.get('status', 'Active'), 'pin': a.get('pin', '0000')}
        for a in spec['accounts']['list']
    ]
    return spec


# ------------------------------------------------------------------ token map
def build_tokens(spec):
    app = spec['app']
    seed = build_seed(spec)
    tokens = {
        'APP_TITLE': app['title'],
        'APP_TITLE_UPPER': title_upper(app['title']),
        'APP_NAME': app['name'],
        'SHORT_NAME': app['name'],
        'KIT_TITLE': app['title'],
        'BUSINESS_NAME': app['businessName'],
        'TAGLINE': app['tagline'],
        'BRAND_SLUG': app['slug'],
        'BRAND_UPPER': re.sub(r'[^A-Z0-9]', '_', app['slug'].upper()),
        'ARTIFACT_SLUG': re.sub(r'[^A-Za-z0-9]+', '-', app['name']).strip('-'),
        'ANDROID_APP_ID': app['androidAppId'],
        'APP_INITIALS': app['initials'],
        'ANDROID_PACKAGE': app['androidAppId'].replace('.', '-'),
        'ELECTRON_NAME': app['slug'] + '-desktop',
        'APP_VERSION': app['version'],
        'AUTHOR': app['author'],
        'AUTHOR_EMAIL': app['authorEmail'],
        'YEAR': time.strftime('%Y'),
        'THEME_COLOR': app['themeColor'],
        'BG_HEX': app['themeColor'].lstrip('#'),
        'ORIENTATION': app['orientation'],
        'KEYSTORE_NAME': app['slug'],
        'KEYSTORE_ALIAS': app['keystoreAlias'],
        'OWNER_USER': spec['accounts']['ownerUsername'],
        'OWNER_PASSWORD': spec['accounts']['ownerPassword'],
        'TIME_ZONE': app['timeZone'],
        'SPREADSHEET_ID': app['spreadsheetId'],
        'DEFAULT_TOKEN': spec['token']['default'],
        'SEED_JS': json.dumps(seed, indent=2, ensure_ascii=False),
        'CURRENCY': spec['settings'].get('Currency', '₱'),
        'METHOD_LIST': ', '.join(spec['paymentMethods']),
        'METHOD_1': spec['paymentMethods'][0],
        'METHOD_2': spec['paymentMethods'][1] if len(spec['paymentMethods']) > 1 else spec['paymentMethods'][0],
        'METHOD_3': spec['paymentMethods'][2] if len(spec['paymentMethods']) > 2 else spec['paymentMethods'][-1],
        'NONREV_LABELS': ', '.join(t['label'] for t in spec['nonRevenueTypes']) or 'none configured',
        'NONREV_1': spec['nonRevenueTypes'][0]['label'] if spec['nonRevenueTypes'] else '',
        'NONREV_2': spec['nonRevenueTypes'][1]['label'] if len(spec['nonRevenueTypes']) > 1 else '',
        'QUEUE_RANGE': f"{spec['queue']['min']} to {spec['queue']['max']}",
        'MOCK_PORT': '8765',
        'GENERATED_AT': time.strftime('%Y-%m-%d'),
        'ACCOUNT_TABLE': '\n'.join(
            f"| `{a['username']}` | `{a['password']}` | {next((r['name'] for r in spec['roles'] if r['id'] == a['roleId']), a['roleId'])} | {a['branchId']} |"
            for a in spec['accounts']['list']),
        'PRODUCT_COUNT': str(len(spec['products'])),
        'BRANCH_COUNT': str(len(spec['branches'])),
        'ROLE_COUNT': str(len(spec['roles'])),
        'ACCOUNT_COUNT': str(len(spec['accounts']['list'])),
        'BUSINESS_BRANCH_1': spec['branches'][0]['name'] if spec['branches'] else '',
        'BUSINESS_BRANCH_2': spec['branches'][1]['name'] if len(spec['branches']) > 1 else '',
        'BUSINESS_BRANCH_3': spec['branches'][2]['name'] if len(spec['branches']) > 2 else '',
        'OWNER_USER': spec['accounts']['ownerUsername'],
        'CASHIER_USER': spec['accounts']['list'][1]['username'] if len(spec['accounts']['list']) > 1 else spec['accounts']['ownerUsername'],
        'CASHIER_PASSWORD': spec['accounts']['list'][1]['password'] if len(spec['accounts']['list']) > 1 else spec['accounts']['ownerPassword'],
        'KITCHEN_USER': spec['accounts']['list'][-1]['username'],
        'KITCHEN_PASSWORD': spec['accounts']['list'][-1]['password'],
        'MANAGER_USER': spec['accounts']['list'][-1]['username'],
        'MANAGER_PASSWORD': spec['accounts']['list'][-1]['password'],
    }
    return tokens


def build_seed(spec):
    """The single seed object shared by app/js/seed.js and Installer.gs."""
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    branches = [[b['id'], b['name'], b.get('address', ''), b.get('contact', ''),
                 b.get('status', 'Active'), spec['queue']['min'], spec['queue']['max'],
                 b.get('note', '')] for b in spec['branches']]

    roles = [[r['id'], r['name'], r.get('description', ''), ','.join(r['permissions']),
              'Yes' if r.get('isAdmin') else 'No',
              'Yes' if r.get('canPOS', True) else 'No',
              'Yes' if r.get('canKitchen', True) else 'No',
              'Yes' if r.get('allBranches') else 'No',
              'Yes' if r.get('canManage') else 'No',
              'Yes' if r.get('canExport') else 'No'] for r in spec['roles']]

    def account_row(a):
        salt = hashlib.sha256((a['username'] + ':' + a['accountId']).encode()).hexdigest()[:12]
        return [a['accountId'], a['username'], a['displayName'], a['roleId'], a['branchId'],
                a.get('status', 'Active'), salt,
                'sha256$' + hashlib.sha256((salt + '|' + a['password']).encode()).hexdigest(),
                'sha256$' + hashlib.sha256((salt + '|' + a.get('pin', '0000')).encode()).hexdigest()]

    accounts = [account_row(a) for a in spec['accounts']['list']]

    products = [[p['id'], p['name'], p.get('category', ''), p['price'],
                 p.get('volumeLabel', ''), p.get('volume', []), p.get('contents', ''),
                 p.get('status', 'Active'), p.get('sort', (i + 1) * 10)]
                for i, p in enumerate(spec['products'])]

    settings = [[k, str(v), spec.get('settingNotes', {}).get(k, '')]
                for k, v in spec['settings'].items()]

    return {
        'generated': now,
        'app': {'slug': spec['app']['slug'], 'title': spec['app']['title'],
                'businessName': spec['app']['businessName'], 'version': spec['app']['version']},
        'branches': branches,
        'roles': roles,
        'accounts': accounts,
        'products': products,
        'categories': [[c['id'], c['name'], c.get('icon', '•')] for c in spec['categories']],
        'settings': settings,
        'paymentMethods': spec['paymentMethods'],
        'nonRevenueTypes': [{'label': t['label'], 'badge': t.get('badge', 'warn'),
                             'hint': t.get('hint', '')} for t in spec['nonRevenueTypes']],
        'queue': {'min': spec['queue']['min'], 'max': spec['queue']['max']},
        'features': spec['features'],
    }


def render(text, tokens, source='templates'):
    def sub(m):
        key = m.group(1)
        if key not in tokens:
            fail('UNKNOWN_PLACEHOLDER',
                 f'{source}: placeholder {{{{${key}}}}} has no value.',
                 'This is a kit bug: the template asks for a value the generator does not produce.')
        return str(tokens[key])
    return re.sub(r'\{\{([A-Z0-9_]+)\}\}', sub, text)


# ------------------------------------------------------------------ generators
def annotate_accounts(spec):
    """Compute salt + hashes once so the app seed, the workbook and Apps Script
    all agree on the same first sign-in. Plain passwords never leave this step."""
    for a in spec['accounts']['list']:
        salt = hashlib.sha256((a['username'] + ':' + a['accountId']).encode()).hexdigest()[:12]
        a['passwordSalt'] = salt
        a['passwordHash'] = 'sha256$' + hashlib.sha256((salt + '|' + a['password']).encode()).hexdigest()
        a['pinHash'] = 'sha256$' + hashlib.sha256((salt + '|' + a.get('pin', '0000')).encode()).hexdigest()
    return spec


def generate(spec, out_dir, force=False, skip_excel=False, skip_icons=False, dry_run=False):
    """Write a complete project into out_dir. Returns a report dict."""
    started = time.time()
    tokens = build_tokens(spec)
    files = []

    if os.path.exists(out_dir) and os.listdir(out_dir):
        if not force:
            fail('OUT_DIR_NOT_EMPTY',
                 f'{out_dir} already contains files.',
                 'Pass --force to overwrite, or choose a new --out folder.',
                 exit_code=EXIT_GATE)
        if dry_run:
            files.append('<would overwrite existing folder>')

    def target(rel):
        return os.path.join(out_dir, rel)

    def write(rel, content):
        if dry_run:
            files.append(rel)
            return
        path = target(rel)
        os.makedirs(os.path.dirname(path) or out_dir, exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
        files.append(rel)

    # ---- 1. render every flat template into its decoded destination path
    templates = template_files()
    if not templates:
        fail('TEMPLATES_MISSING',
             f'No templates found beside the tool ({TEMPLATE_PREFIX}*).',
             'Re-download the kit as a whole folder; the asset-tpl-* files are required.')
    for rel, src in sorted(templates.items()):
        with open(src, encoding='utf-8') as f:
            body = f.read()
        write(rel, render(body, tokens, rel))

    # ---- 1b. the seed is generated, not templated: one compact object shared by
    # the browser and the Apps Script installer (same arrays, same hashes).
    write('app/js/seed.js',
          '/* =====================================================================\n'
          '   {{KIT_TITLE}} - first-run seed data\n'
          '   Generated by offline-sheets-app-kit from the specification file.\n'
          '   Consumed by app.js (browser) and Installer.gs (server) so both agree.\n'
          '   Re-generate instead of hand-editing.\n'
          '   ===================================================================== */\n'
          '(function (global) {\n'
          "  'use strict';\n"
          '  global.KIT_SEED = {{SEED_JS}};\n'
          '})(window);\n'
          .replace('{{KIT_TITLE}}', tokens['KIT_TITLE'])
          .replace('{{SEED_JS}}', tokens['SEED_JS']))

    # ---- 2. project README + docs
    write('README.md', render(read_asset('asset-readme-project.md'), tokens))
    write('docs/00-quick-start.md', render(read_asset('asset-doc-quick-start.md'), tokens))
    write('docs/01-google-sheets-setup.md', render(read_asset('asset-doc-google-sheets.md'), tokens))
    write('docs/02-using-the-app.md', render(read_asset('asset-doc-using-the-app.md'), tokens))
    write('docs/03-android-build.md', render(read_asset('asset-doc-android.md'), tokens))
    write('docs/04-windows-build.md', render(read_asset('asset-doc-windows.md'), tokens))
    write('docs/05-spec-reference.md', render(read_asset('asset-doc-spec-reference.md'), tokens))
    write('docs/06-troubleshooting.md', render(read_asset('asset-doc-troubleshooting.md'), tokens))
    write('start-mock-server.sh', render(read_asset('asset-start-mock-server.sh'), tokens))
    write('tools/mock-server.js', render(read_asset('asset-mock-server.js'), tokens))
    write('tools/build_excel.py', read_tool('tools-build-excel-template.py'))
    write('tools/build_single_file.py', read_tool('tools-build-single-file.py'))
    write('tools/make_icons.py', read_tool('tools-make-icons.py'))
    write('tools/generate-token.sh', render(read_asset('asset-generate-token.sh'), tokens))
    write('tools/test_app.py', render(read_asset('asset-test-app.py'), tokens))
    write('tools/test_sync.py', render(read_asset('asset-test-sync.py'), tokens))
    write('tools/test_edge.py', render(read_asset('asset-test-edge.py'), tokens))
    write('.gitignore', read_asset('asset-gitignore.txt'))
    write('dist/README.txt', render(read_asset('asset-dist-readme.txt'), tokens))

    report = {'ok': True, 'out': os.path.abspath(out_dir), 'files': len(files),
              'spec': os.path.abspath(spec.get('_source', '')) if spec.get('_source') else '(inline)',
              'steps': []}

    # ---- 3. Excel workbook
    if not skip_excel and not dry_run:
        xlsx = target('excel/' + tokens['ARTIFACT_SLUG'] + '-Google-Sheet-Template.xlsx')
        ok, info = run_tool([sys.executable, os.path.join(HERE, 'tools-build-excel-template.py'),
                             '--spec', spec['_spec_path'], '--out', xlsx],
                            'excel', TIMEOUTS['excel'], RETRIES['excel'])
        if not ok:
            fail('EXCEL_FAILED', 'The Google Sheets workbook could not be built.', info)
        files.append(os.path.relpath(xlsx, out_dir))
        report['steps'].append({'step': 'excel', 'ok': True, 'sheets': info.get('sheets')})

    # ---- 4. icons
    if not skip_icons and not dry_run:
        ok, info = run_tool([sys.executable, os.path.join(HERE, 'tools-make-icons.py'),
                             '--spec', spec['_spec_path'], '--out', os.path.abspath(out_dir)],
                            'icons', TIMEOUTS['icons'], RETRIES['icons'])
        if not ok:
            fail('ICONS_FAILED', 'Icons and splash screens could not be generated.', info)
        report['steps'].append({'step': 'icons', 'ok': True, 'written': info.get('written')})

    # ---- 5. single-file build
    if not dry_run:
        single = target('app/' + tokens['ARTIFACT_SLUG'] + '.html')
        ok, info = run_tool([sys.executable, os.path.join(HERE, 'tools-build-single-file.py'),
                             '--app', os.path.abspath(os.path.join(out_dir, 'app')),
                             '--out', single], 'single_file', TIMEOUTS['single_file'], 1)
        if not ok:
            fail('SINGLE_FILE_FAILED', 'The single-file build failed.', info)
        files.append(os.path.relpath(single, out_dir))
        report['steps'].append({'step': 'single_file', 'ok': True, 'bytes': info.get('bytes')})

    # ---- 6. JavaScript syntax check
    if not dry_run and shutil.which('node'):
        js_files = [p for p in files if p.endswith('.js') or p.endswith('.gs')]
        bad = []
        for rel in js_files:
            # node --check refuses the .gs extension, so copy to a temp .js
            staged = target(rel)
            if rel.endswith('.gs'):
                import tempfile as _tf
                fd, staged = _tf.mkstemp(suffix='.js')
                os.close(fd)
                shutil.copyfile(target(rel), staged)
            ok, info = run_tool(['node', '--check', staged], 'node_check',
                                TIMEOUTS['node_check'], RETRIES['node_check'])
            if rel.endswith('.gs') and os.path.exists(staged):
                os.unlink(staged)
            if not ok:
                bad.append({'file': rel, 'error': (info.get('stderr') or '').strip().split('\n')[:4]})
        if bad:
            fail('JS_SYNTAX', 'Generated JavaScript failed the syntax check.', details=bad)
        report['steps'].append({'step': 'node_check', 'ok': True, 'files': len(js_files)})

    report['files'] = len(files)
    report['fileList'] = sorted(files)[:400]
    report['elapsedMs'] = int((time.time() - started) * 1000)
    report['next'] = [
        f'cd {out_dir}',
        'python tools/build_single_file.py --app app --out app/' + tokens['ARTIFACT_SLUG'] + '.html',
        'open app/index.html   # or the single-file HTML',
        'read README.md, then docs/01-google-sheets-setup.md',
    ]
    return report


def template_files():
    """Map each asset-tpl-* filename to the destination path it renders to."""
    out = {}
    for name in os.listdir(HERE):
        if not name.startswith(TEMPLATE_PREFIX) or not os.path.isfile(os.path.join(HERE, name)):
            continue
        rel = name[len(TEMPLATE_PREFIX):].replace('__', '/')
        out[rel] = os.path.join(HERE, name)
    return out


def read_asset(name):
    path = os.path.join(HERE, name)
    if not os.path.isfile(path):
        fail('ASSET_MISSING', f'Kit file missing: {name}',
             'The kit folder is incomplete; re-download it as a whole folder.')
    with open(path, encoding='utf-8') as f:
        return f.read()


def read_tool(name):
    return read_asset(name)


def run_tool(cmd, step, timeout, retries):
    """Run a subprocess with a hard timeout and limited retries."""
    last = None
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = {'step': step, 'error': f'timed out after {timeout}s', 'attempt': attempt + 1}
            continue
        except FileNotFoundError as e:
            fail('DEPENDENCY_MISSING', f'Could not start {cmd[0]}: {e.strerror}',
                 'Install the missing dependency and re-run.', exit_code=EXIT_DEP)
        if proc.returncode == 0:
            try:
                return True, json.loads(proc.stdout or '{}')
            except json.JSONDecodeError:
                return True, {'stdout': proc.stdout[-2000:]}
        last = {'step': step, 'returncode': proc.returncode,
                'stderr': (proc.stderr or proc.stdout or '')[-2000:], 'attempt': attempt + 1}
        if 'pip install' in (proc.stderr or ''):
            fail('DEPENDENCY_MISSING', f'{step}: {proc.stderr.strip().splitlines()[-1]}',
                 'Install it with:  pip install openpyxl pillow', exit_code=EXIT_DEP)
    return False, last


# ------------------------------------------------------------------ commands
def cmd_list(args):
    examples = sorted(n for n in os.listdir(HERE)
                      if n.endswith('.json') and n.startswith('asset-example-spec'))
    schema = json.load(open(SCHEMA_PATH, encoding='utf-8')) if os.path.isfile(SCHEMA_PATH) else {}
    print(json.dumps({
        'kit': GENERATOR, 'version': VERSION,
        'examples': [{'file': e, 'app': json.load(open(os.path.join(HERE, e), encoding='utf-8'))
                      ['app']['name']} for e in examples],
        'specFields': sorted(schema.get('properties', {})),
        'limits': LIMITS,
        'commands': ['list', 'validate', 'new', 'build-single-file', 'describe', 'help'],
    }, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_validate(args):
    path = args.spec
    spec = load_json(path)
    errors, warnings = validate_spec(spec)
    out = {'ok': not errors, 'spec': os.path.abspath(path), 'errors': errors, 'warnings': warnings}
    if not errors:
        s = normalise(json.loads(json.dumps(spec)))
        seed = build_seed(s)
        out['summary'] = {
            'app': s['app']['title'], 'slug': s['app']['slug'],
            'branches': len(s['branches']), 'roles': len(s['roles']),
            'accounts': len(s['accounts']['list']), 'products': len(s['products']),
            'paymentMethods': s['paymentMethods'],
            'nonRevenueTypes': [t['label'] for t in s['nonRevenueTypes']],
            'features': {k: v for k, v in s['features'].items()},
            'settingsRows': len(s['settings']),
            'seedRows': sum(len(seed[k]) for k in ('branches', 'roles', 'accounts', 'products', 'settings')),
        }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return EXIT_OK if not errors else EXIT_VALID


def cmd_describe(args):
    spec = normalise(load_json(args.spec))
    tokens = build_tokens(spec)
    print(json.dumps({
        'app': spec['app'],
        'tokenNames': sorted(k for k in tokens if k != 'SEED_JS'),
        'templateFiles': sorted(template_files()),
    }, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_new(args):
    out = os.path.abspath(args.out)
    if len(out) > LIMITS['out_path_max_len']:
        fail('OUT_PATH_TOO_LONG', f'--out is {len(out)} characters; keep it under {LIMITS["out_path_max_len"]}.',
             'Move the kit to a shorter folder.', exit_code=EXIT_ARGS)
    spec = load_json(args.spec)
    errors, warnings = validate_spec(spec)
    if errors:
        print(json.dumps({'ok': False, 'error': {'code': 'SPEC_INVALID',
                                                 'message': f'{len(errors)} problem(s) in the specification',
                                                 'details': errors}}, indent=2, ensure_ascii=False))
        return EXIT_VALID
    spec = normalise(json.loads(json.dumps(spec)))
    spec = annotate_accounts(spec)

    # The workbook and installer builders need the resolved spec (defaults filled,
    # hashes computed) as a file. It is written to the system temp folder and
    # removed in the finally block, so the delivered project stays clean.
    tmp_dir = tempfile.mkdtemp(prefix='kit-spec-')
    resolved_path = os.path.join(tmp_dir, 'resolved-spec.json')
    with open(resolved_path, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in spec.items() if not k.startswith('_')}, f,
                  ensure_ascii=False, indent=1)
    spec['_spec_path'] = resolved_path
    try:
        report = generate(spec, out, force=args.force, skip_excel=args.skip_excel,
                          skip_icons=args.skip_icons, dry_run=args.dry_run)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    report['warnings'] = warnings
    report['credentials'] = {
        'owner': f"{spec['accounts']['ownerUsername']} / {spec['accounts']['ownerPassword']}",
        'accounts': [{'username': a['username'], 'password': a['password'], 'role': a['roleId'],
                      'branch': a['branchId']} for a in spec['accounts']['list']],
    }
    report['syncToken'] = spec['token']['default']
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_build_single_file(args):
    project = os.path.abspath(args.project)
    app_dir = os.path.join(project, 'app')
    if not os.path.isdir(app_dir):
        fail('PROJECT_NOT_FOUND', f'No app/ folder inside {project}',
             'Point --project at a folder produced by "new".', exit_code=EXIT_ARGS)
    slug = slugify(os.path.basename(project))
    out = args.out or os.path.join(app_dir, slug + '.html')
    ok, info = run_tool([sys.executable, os.path.join(HERE, 'tools-build-single-file.py'),
                         '--app', app_dir, '--out', os.path.abspath(out)],
                        'single_file', TIMEOUTS['single_file'], 1)
    print(json.dumps({'ok': ok, 'result': info}, indent=2))
    return EXIT_OK if ok else EXIT_FAIL


def cmd_help(args):
    short = not getattr(args, 'long', False)
    text = __doc__ if not short else __doc__.split('QUICK START')[0]
    print(text.strip())
    if not short:
        print('\nCAPABILITIES')
        for line in [
            'list                 - show bundled example specs, spec fields and hard limits',
            'validate --spec F    - check a spec; prints every error with its JSON path',
            'new --spec F --out D - generate the whole project (app, backend, sheet, builds, docs)',
            'build-single-file    - inline an existing project into one HTML file',
            'describe --spec F    - show the resolved app values and template placeholders',
            'help --long          - full reference',
        ]:
            print('  ' + line)
        print('\nAPPROVAL GATES')
        print('  --force is required to write into a non-empty --out folder.')
        print('  The generator never deploys, never uploads and never contacts Google.')
        print('  The emitted ACCESS_TOKEN is CHANGE-ME-<random> and the backend rejects it')
        print('  until a human replaces it in Config.gs.')
        print('\nLIMITS')
        for k, v in LIMITS.items():
            print(f'  {k}: {v}')
        print('\nTIMEOUTS (seconds): ' + ', '.join(f'{k}={v}' for k, v in TIMEOUTS.items()))
        print('RETRIES: ' + ', '.join(f'{k}={v}' for k, v in RETRIES.items()))
        print('\nCLEANUP: partial output is left in place for inspection and the path is')
        print('  printed in the error, so nothing is silently deleted.')
    return EXIT_OK


# ------------------------------------------------------------------ entry
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help', 'help') and '--long' not in argv:
        if argv and argv[0] == 'help':
            return cmd_help(argparse.Namespace(long='--long' in argv))
        print(__doc__.split('QUICK START')[0].strip())
        print('\nRun "python tools-generate-kit.py help --long" for the full reference.')
        return EXIT_OK

    parser = argparse.ArgumentParser(prog='tools-generate-kit.py', add_help=False,
                                     description='Generate an offline-first, Google-Sheets-backed app.')
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('list', add_help=False)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('validate', add_help=False)
    p.add_argument('--spec', required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser('describe', add_help=False)
    p.add_argument('--spec', required=True)
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser('new', add_help=False)
    p.add_argument('--spec', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--force', action='store_true', help='overwrite a non-empty --out folder')
    p.add_argument('--skip-excel', action='store_true')
    p.add_argument('--skip-icons', action='store_true')
    p.add_argument('--dry-run', action='store_true', help='list the files that would be written')
    p.set_defaults(func=cmd_new)

    p = sub.add_parser('build-single-file', add_help=False)
    p.add_argument('--project', required=True)
    p.add_argument('--out')
    p.set_defaults(func=cmd_build_single_file)

    p = sub.add_parser('help', add_help=False)
    p.add_argument('--long', action='store_true')
    p.set_defaults(func=cmd_help)

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_ARGS

    if not getattr(args, 'command', None):
        print(__doc__.split('QUICK START')[0].strip())
        return EXIT_OK

    try:
        return args.func(args)
    except KitError as e:
        print(json.dumps(e.as_dict(), indent=2, ensure_ascii=False))
        return e.exit_code
    except KeyboardInterrupt:
        print(json.dumps({'ok': False, 'error': {'code': 'CANCELLED',
                                                 'message': 'Stopped by the user.'}}))
        return EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
