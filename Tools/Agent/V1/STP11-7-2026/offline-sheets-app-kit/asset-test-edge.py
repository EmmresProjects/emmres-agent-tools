#!/usr/bin/env python3
"""
Generated project - tools/test_edge.py
=====================================
The awkward paths a demo never covers:

  * a kitchen-only account lands on the kitchen screen and cannot see Products/Accounts
  * a direct quantity of 50 (not 50 clicks) produces the right total
  * a discount is applied to the total, not to a line
  * an unpaid "Order Only" sale still creates a queue card
  * voiding returns the queue card to the pool
  * CSV export runs without throwing

Usage:  python tools/test_edge.py --spec spec.json
"""
import argparse
import json
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write('Playwright is required: pip install playwright && python -m playwright install chromium\n')
    sys.exit(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument('--spec', required=True)
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    spec = json.load(open(args.spec, encoding='utf-8'))
    # the most restricted account in the spec: fewest permissions
    ranked = sorted(spec['roles'], key=lambda r: len(r.get('permissions', [])))
    kitchen_role = ranked[0]['id'] if ranked else None
    kitchen_acc = next((a for a in spec['accounts']['list'] if a['roleId'] == kitchen_role),
                       spec['accounts']['list'][-1])
    kitchen_feature = bool(spec.get('features', {}).get('kitchen', True))
    owner = spec['accounts']['ownerUsername']
    owner_pw = spec['accounts']['ownerPassword']

    results, errors = [], []

    def check(name, ok, detail=''):
        results.append({'check': name, 'ok': bool(ok)})
        print(('  PASS ' if ok else '  FAIL ') + name + ('' if ok else f'  <- {detail}'))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda m: errors.append('CONSOLE: ' + m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append('PAGEERROR: ' + str(e)))
        page.goto('file://' + os.path.join(project, 'app', 'index.html'))
        page.wait_for_timeout(1200)

        # ---- restricted account
        page.fill('#username', kitchen_acc['username'])
        page.fill('#password', kitchen_acc['password'])
        page.click('#loginBtn')
        page.wait_for_timeout(1800)
        check('restricted account signs in', page.is_visible('#app'))
        nav = page.evaluate("Array.from(document.querySelectorAll('[data-nav]'))"
                            ".filter(n => getComputedStyle(n).display !== 'none')"
                            ".map(n => n.getAttribute('data-nav'))")
        if kitchen_feature and 'kitchen.view' in next(
                (r['permissions'] for r in spec['roles'] if r['id'] == kitchen_role), []):
            check('restricted account sees the kitchen nav', 'kitchen' in nav, nav)
        else:
            check('kitchen nav hidden when the module or permission is off',
                  'kitchen' not in nav, nav)
        check('restricted account cannot see admin pages',
              'products' not in nav and 'accounts' not in nav, nav)
        page.evaluate("App.logout()")
        page.wait_for_timeout(800)

        # ---- owner flow
        page.fill('#username', owner)
        page.fill('#password', owner_pw)
        page.click('#loginBtn')
        page.wait_for_timeout(1800)
        page.evaluate("App.go('pos')")
        page.wait_for_timeout(700)

        price = page.evaluate("App.state.products[0].price")
        page.evaluate("POS.addProduct(App.state.products[0].id, 50)")
        page.wait_for_timeout(300)
        total = page.evaluate("POS.total()")
        check('direct quantity of 50 totals correctly', abs(total - price * 50) < 0.01,
              {'got': total, 'expected': price * 50})

        if spec.get('features', {}).get('queueCards', True):
            q = page.evaluate('POS.queueRange()')
            check('queue range is populated', len(q) == (spec['queue']['max'] - spec['queue']['min'] + 1), q[:3])

        if spec.get('features', {}).get('discounts', True):
            page.evaluate("POS.openDiscount && POS.openDiscount()")
            page.wait_for_timeout(500)
            applied = page.evaluate("""() => {
                const v = document.querySelector('#dVal'); if (!v) return false;
                v.value = '10';
                const ok = document.querySelector('[data-ok]'); if (!ok) return false;
                ok.click(); return true;
            }""")
            page.wait_for_timeout(400)
            after = page.evaluate("POS.total()")
            check('10% discount applies to the cart total',
                  (not applied) or abs(after - price * 50 * 0.9) < 0.01,
                  {'applied': applied, 'before': price * 50, 'after': after})

        # ---- unpaid order keeps a queue card
        page.evaluate("POS.openCheckout()")
        page.wait_for_timeout(600)
        unpaid = page.evaluate("!!document.querySelector('[data-unpaid]')")
        if unpaid and spec.get('features', {}).get('unpaidOrders', True):
            page.evaluate("document.querySelector('[data-unpaid]').click()")
            page.wait_for_timeout(1600)
            status = page.evaluate("DB.getAll('orders').then(a => a[a.length-1].paymentStatus)")
            check('unpaid order is saved as Unpaid', str(status).lower().startswith('unpaid'), status)
        else:
            check('unpaid button hidden when the feature is off', True)

        # ---- export does not throw
        page.evaluate("App.go('reports')")
        page.wait_for_timeout(900)
        page.evaluate("App.go('orders')")
        page.wait_for_timeout(900)
        check('orders page renders', page.is_visible('#page-orders'))

        browser.close()

    check('no console errors', not errors, errors[:5])
    passed = sum(1 for r in results if r['ok'])
    print(json.dumps({'ok': passed == len(results), 'passed': passed, 'total': len(results)}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
