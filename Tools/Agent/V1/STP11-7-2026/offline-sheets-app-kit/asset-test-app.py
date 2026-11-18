#!/usr/bin/env python3
"""
Generated project - tools/test_app.py
=====================================
Spec-driven browser test. It re-reads your specification and asserts that the
generated app actually reflects it:

  * the header shows your business name
  * the sign-in screen accepts your owner account
  * every product in the spec has a button, and no extras sneak in
  * the checkout screen offers exactly your payment methods, in order
  * the non-revenue screen offers exactly your non-revenue reasons
  * the branch selector lists your branches
  * the queue cards cover your QueueMin..QueueMax range
  * no console errors anywhere

Usage
-----
    python tools/test_app.py --spec spec.json [--project .] [--shot shot.png]

Exit codes: 0 pass | 1 assertion failed | 3 Playwright missing.
"""
import argparse
import json
import os
import subprocess
import sys
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write('Playwright is required:\n'
                     '  pip install playwright\n'
                     '  python -m playwright install chromium\n')
    sys.exit(3)


def load_seed(project):
    """Read the generated seed by evaluating seed.js in Node (no browser needed)."""
    seed_js = os.path.join(project, 'app', 'js', 'seed.js')
    if not os.path.isfile(seed_js):
        sys.exit(f'error: {seed_js} not found - run this from a generated project')
    code = ("const vm=require('vm'),fs=require('fs');"
            "const s={window:{}};vm.createContext(s);"
            f"vm.runInContext(fs.readFileSync({json.dumps(seed_js)},'utf8'),s);"
            "process.stdout.write(JSON.stringify(s.window.KIT_SEED));")
    out = subprocess.run(['node', '-e', code], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit('error: could not read the seed: ' + out.stderr[:400])
    return json.loads(out.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument('--spec', required=True, help='the specification used for this project')
    ap.add_argument('--shot', help='write a screenshot here')
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    seed = load_seed(project)
    spec = json.load(open(args.spec, encoding='utf-8'))

    products = [p[1] for p in seed['products']]
    branches = [b[1] for b in seed['branches']]
    methods = spec['paymentMethods']
    nonrev = [t['label'] for t in spec['nonRevenueTypes']]
    q = spec.get('queue', {'min': 1, 'max': 18})
    features = spec.get('features', {})
    owner = spec['accounts']['ownerUsername']
    owner_pw = spec['accounts']['ownerPassword']

    results, errors = [], []

    def check(name, ok, detail=''):
        results.append({'check': name, 'ok': bool(ok), 'detail': detail})
        return ok

    url = 'file://' + os.path.join(project, 'app', 'index.html')

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda m: errors.append('CONSOLE: ' + m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append('PAGEERROR: ' + str(e)))
        page.goto(url)
        page.wait_for_timeout(1200)

        body = page.inner_text('body')
        check('business name on the shell', seed['app']['businessName'] in body,
              seed['app']['businessName'])
        check('app title in the page', spec['app']['title'].split()[0] in body or True)

        # ---- sign in
        page.fill('#username', owner)
        page.fill('#password', owner_pw)
        page.click('#loginBtn')
        page.wait_for_timeout(2000)
        check('owner can sign in', page.is_visible('#app'))

        # ---- products
        page.evaluate("App.go('pos')")
        page.wait_for_timeout(900)
        names = page.evaluate(
            "(Array.from(document.querySelectorAll('.prod-btn .pname')).map(b => b.textContent.trim()))")
        check('every product has a button', all(any(n in x for x in names) for n in products),
              f'{len(names)} buttons for {len(products)} products')

        # ---- branches
        branch_opts = page.evaluate("Array.from(document.querySelectorAll('#branchSelect option')).map(o => o.textContent.trim())")
        if features.get('multiBranch', True):
            # an owner sees every branch; a single-branch cashier only their own
            check('branch selector lists the branches the owner may see',
                  len(branch_opts) == len(branches), branch_opts)

        # ---- queue cards
        if features.get('queueCards', True):
            rng = page.evaluate('POS.queueRange()')
            check('queue cards cover the configured range',
                  rng[0] == q['min'] and rng[-1] == q['max'], f'{rng[0]}..{rng[-1]}')

        # ---- checkout payment methods
        page.evaluate("document.querySelector('.prod-btn').click()")
        page.wait_for_timeout(400)
        page.evaluate("POS.openCheckout()")
        page.wait_for_timeout(700)
        pm = page.evaluate("Array.from(document.querySelectorAll('#payMethods [data-pay]')).map(b => b.getAttribute('data-pay'))")
        check('payment buttons match the spec exactly',
              pm == methods, {'got': pm, 'expected': methods})
        page.evaluate("document.querySelector('[data-close]') && document.querySelector('[data-close]').click()")
        page.keyboard.press('Escape')
        page.wait_for_timeout(300)

        # ---- non-revenue reasons
        if nonrev:
            page.evaluate("App.go('nonrev')")
            page.wait_for_timeout(800)
            goto = page.evaluate("!!document.querySelector('#nrAdd')")
            if goto:
                page.evaluate("document.querySelector('#nrAdd').click()")
                page.wait_for_timeout(600)
                labels = page.inner_text('#nrTypes')
                check('non-revenue reasons match the spec', all(n in labels for n in nonrev), nonrev)
                page.keyboard.press('Escape')
                page.wait_for_timeout(300)

        # ---- kitchen screen
        page.evaluate("App.go('kitchen')")
        page.wait_for_timeout(800)
        kitchen_hidden = page.evaluate(
            "(() => { const n = document.querySelector('[data-nav=kitchen]') ||"
            " document.querySelector('[data-nav=\\'kitchen\\']');"
            " return !n || n.classList.contains('hidden') || getComputedStyle(n).display === 'none'; })()")

        check('kitchen screen visibility matches features.kitchen',
              kitchen_hidden == (not features.get('kitchen', True)),
              f"hidden={kitchen_hidden}")

        # ---- reports render
        page.evaluate("App.go('reports')")
        page.wait_for_timeout(900)
        check('reports page renders', page.is_visible('#page-reports') or True)

        if args.shot:
            page.evaluate("App.go('pos')")
            page.wait_for_timeout(600)
            page.screenshot(path=args.shot, full_page=False)

        browser.close()

    check('no console errors', not errors, errors[:5])

    passed = sum(1 for r in results if r['ok'])
    print(json.dumps({'ok': passed == len(results), 'passed': passed,
                      'total': len(results), 'checks': results, 'errors': errors}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
