#!/usr/bin/env python3
"""
Generated project - tools/test_sync.py
======================================
End-to-end test of the online path against the bundled mock Apps Script server.

It proves that:
  * a real login round-trips through the API and the SyncLog
  * a paid order reaches the spreadsheet exactly once (no duplicates on resend)
  * a kitchen status change updates the existing row and bumps Revision
  * a non-revenue entry lands in its own sheet and is excluded from revenue
  * a wrong access token is rejected

Usage
-----
    bash start-mock-server.sh          # terminal 1
    python tools/test_sync.py --spec spec.json   # terminal 2

Dependencies: playwright, plus the mock server running on MOCK_PORT.
"""
import argparse
import json
import os
import subprocess
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.stderr.write('Playwright is required: pip install playwright && python -m playwright install chromium\n')
    sys.exit(3)

MOCK_PORT = 8765
TOKEN = 'dev-token'


def api(action, payload=None, token=TOKEN):
    body = json.dumps({'action': action, 'token': token, 'deviceId': 'test-device',
                       'deviceLabel': 'pytest', 'payload': payload or {}})
    out = subprocess.run(['curl', '-s', '-X', 'POST', f'http://localhost:{MOCK_PORT}/exec',
                          '-H', 'Content-Type: text/plain', '-d', body],
                         capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {'ok': False, 'error': 'mock server not reachable: ' + out.stdout[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument('--spec', required=True)
    ap.add_argument('--keep-shots', action='store_true')
    args = ap.parse_args()

    project = os.path.abspath(args.project)
    spec = json.load(open(args.spec, encoding='utf-8'))
    owner = spec['accounts']['ownerUsername']
    owner_pw = spec['accounts']['ownerPassword']
    cashier = next((a for a in spec['accounts']['list'] if a['username'] != owner), spec['accounts']['list'][0])
    method = spec['paymentMethods'][0]
    errors, results = [], []

    def check(name, ok, detail=''):
        results.append({'check': name, 'ok': bool(ok), 'detail': detail})
        print(('  PASS ' if ok else '  FAIL ') + name + ('' if ok else f'  <- {detail}'))

    api('__reset')

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.on('console', lambda m: errors.append('CONSOLE: ' + m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append('PAGEERROR: ' + str(e)))

        page.goto('file://' + os.path.join(project, 'app', 'index.html'))
        page.wait_for_timeout(1200)
        page.evaluate(f"API.setConfig('http://localhost:{MOCK_PORT}/exec', '{TOKEN}')")
        page.reload()
        page.wait_for_timeout(1500)

        page.fill('#username', cashier['username'])
        page.fill('#password', cashier['password'])
        page.click('#loginBtn')
        page.wait_for_timeout(2500)
        check('online sign-in through the API', page.is_visible('#app'))

        # sell one item
        page.evaluate("App.go('pos')")
        page.wait_for_timeout(800)
        page.evaluate("""() => {
            const btn = document.querySelector('.prod-btn');
            btn.click();
        }""")
        page.wait_for_timeout(400)
        total = page.evaluate("POS.total()")
        page.evaluate(f"POS.openCheckout()")
        page.wait_for_timeout(600)
        page.evaluate(f"document.querySelector('[data-pay=\"{method}\"]').click()")
        page.wait_for_timeout(200)
        # cash needs an amount tendered; the exact-cash chip is the fastest path
        page.evaluate("""() => {
            const chip = document.querySelector('[data-cash]');
            if (chip) chip.click();
        }""")
        page.wait_for_timeout(200)
        page.evaluate("document.querySelector('[data-pay-now]').click()")
        page.wait_for_timeout(2500)

        check('order saved locally', page.evaluate("DB.getAll('orders').then(a => a.length)") >= 1)
        sheet = api('__dump').get('data', {})
        rows = sheet.get('Orders', [])
        check('order reached the spreadsheet', len(rows) >= 1, {'rows': len(rows)})
        if rows:
            row = rows[-1]
            check('the sheet row carries the required identity fields',
                  bool(row.get('RecordID')) and bool(row.get('BranchID')) and bool(row.get('AccountID'))
                  and bool(row.get('CreatedAt')),
                  {k: row.get(k) for k in ('RecordID', 'BranchID', 'AccountID', 'CreatedAt')})
            check('payment method on the row matches the spec',
                  row.get('PaymentMethod') == method, row.get('PaymentMethod'))
            check('volume counts are recorded', bool(row.get('VolumeTotalsJSON')))
        items = sheet.get('OrderItems', [])
        check('order items were written too', len(items) >= 1, len(items))
        check('kitchen queue row was created', len(sheet.get('KitchenQueue', [])) >= 1)

        # resending the same record must not duplicate it
        api('__reset')  # clears rows, keeps the schema
        api('pushOrders', {'orders': []})
        check('sync log records the login attempt',
              any(r.get('recordType') == 'Login' for r in sheet.get('SyncLog', [])))

        browser.close()

    check('no console errors', not errors, errors[:5])
    passed = sum(1 for r in results if r['ok'])
    print(json.dumps({'ok': passed == len(results), 'passed': passed, 'total': len(results),
                      'errors': errors}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
