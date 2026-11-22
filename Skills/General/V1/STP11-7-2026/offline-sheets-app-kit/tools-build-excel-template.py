#!/usr/bin/env python3
"""
offline-sheets-app-kit - tools-build-excel-template.py
======================================================
Builds the Google Sheets workbook (.xlsx) for a generated project.

The workbook is the shared record every device syncs into. It is generated
from the same specification that produces the app, so sheet names, headers,
seed rows, dropdown validation and the "START HERE" instructions always match
the running code.

Usage
-----
    python tools-build-excel-template.py --spec asset-spec-example-cafe.json \
        --out ./build/cafe/01-google-sheet.xlsx

    # print the sheet map without writing a file
    python tools-build-excel-template.py --spec spec.json --describe

Dependencies: openpyxl (pip install openpyxl)
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "openpyxl is required:  pip install openpyxl>=3.1\n")
    sys.exit(3)

# ----------------------------------------------------------------- palette
DARK = '1B2635'
ACCENT = 'FFB02E'
HEAD_FONT = Font(bold=True, color='FFFFFF', size=11)
NOTE_FONT = Font(italic=True, size=9, color='6F819A')
BODY = Font(size=10)
HEAD_FILL = PatternFill('solid', fgColor=DARK)
SOFT = PatternFill('solid', fgColor='F2F6FA')
LOCK = PatternFill('solid', fgColor='EDEDED')
THIN = Side(style='thin', color='D9E1EA')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
WRAP = Alignment(wrap_text=True, vertical='top')

def seed_account_rows(spec):
    """The generator passes ready-made salt + hashes inside accounts.list when
    it can, so the workbook and the app agree on the very first sign-in."""
    rows = []
    for acc in spec['accounts']['list']:
        if acc.get('passwordHash'):
            rows.append([acc['accountId'], acc['username'], acc['displayName'], acc['roleId'],
                         acc['branchId'], acc.get('status', 'Active'), acc['passwordSalt'],
                         acc['passwordHash'], acc['pinHash'], datetime.now().replace(microsecond=0),
                         '', 'No', datetime.now().replace(microsecond=0)])
        else:
            salt = acc.get('salt') or ('s' + sha256_hex(acc['username'])[:11])
            now = datetime.now().replace(microsecond=0)
            rows.append([acc['accountId'], acc['username'], acc['displayName'], acc['roleId'],
                         acc['branchId'], acc.get('status', 'Active'), salt,
                         pwd_hash(acc['password'], salt), pwd_hash(acc.get('pin', '0000'), salt),
                         now, '', 'No', now])
    return rows


SHEETS = {
    'Accounts': ['AccountID', 'Username', 'DisplayName', 'RoleID', 'BranchID', 'Status',
                 'PasswordSalt', 'PasswordHash', 'PinHash', 'CreatedAt', 'LastLogin',
                 'ForceChange', 'UpdatedAt'],
    'Roles': ['RoleID', 'RoleName', 'Description', 'Permissions', 'IsAdmin', 'CanUsePOS',
              'CanUseKitchen', 'CanViewAllBranches', 'CanManageData', 'CanExport'],
    'Branches': ['BranchID', 'BranchName', 'Address', 'Contact', 'Status', 'QueueMin',
                 'QueueMax', 'Note'],
    'Products': ['ProductID', 'ProductName', 'Category', 'Price', 'VolumeLabel', 'VolumeJSON',
                 'ComboContents', 'Status', 'SortOrder'],
    'Orders': ['RecordID', 'OrderNo', 'BusinessDate', 'CreatedAt', 'UpdatedAt', 'BranchID',
               'BranchName', 'AccountID', 'CashierName', 'DeviceID', 'QueueNumber', 'OrderType',
               'ItemCount', 'Subtotal', 'DiscountType', 'DiscountValue', 'DiscountAmount', 'Total',
               'PaymentMethod', 'PaymentStatus', 'AmountTendered', 'ChangeDue', 'PaidAt',
               'KitchenStatus', 'KitchenUpdatedAt', 'ServedAt', 'VoidedAt', 'VoidReason',
               'VoidedBy', 'Notes', 'VolumeTotalsJSON', 'Revision', 'SyncStatus', 'ReceivedAt'],
    'OrderItems': ['RecordID', 'LineNo', 'OrderNo', 'BusinessDate', 'BranchID', 'ProductID',
                   'ProductName', 'Qty', 'UnitPrice', 'LineTotal', 'VolumeJSON'],
    'KitchenQueue': ['RecordID', 'OrderNo', 'BusinessDate', 'BranchID', 'QueueNumber',
                     'KitchenStatus', 'PreviousStatus', 'UpdatedAt', 'UpdatedBy', 'Note'],
    'NonRevenueLogs': ['RecordID', 'LogType', 'BusinessDate', 'CreatedAt', 'BranchID', 'LoggedBy',
                       'LoggedByName', 'ItemsJSON', 'VolumeTotalsJSON', 'Note', 'TotalQty',
                       'ReceivedAt'],
    'Settings': ['Key', 'Value', 'Note'],
    'Reports': ['ReportDate', 'BranchID', 'PaidRevenue', 'PaymentBreakdownJSON', 'UnpaidTotal',
                'VoidedTotal', 'OrderCount', 'AvgOrder', 'VolumeJSON', 'GeneratedAt'],
    'SyncLog': ['SyncID', 'RecordID', 'RecordType', 'DeviceID', 'DeviceLabel', 'SentAt', 'Result',
                'Note', 'BranchID', 'AccountID'],
}

WIDTHS = {
    'RecordID': 38, 'SyncID': 38, 'OrderNo': 24, 'DisplayName': 24, 'Username': 14,
    'PasswordHash': 72, 'PinHash': 72, 'PasswordSalt': 16, 'Permissions': 70,
    'Description': 48, 'BranchName': 26, 'Address': 30, 'ProductName': 34,
    'VolumeJSON': 44, 'ComboContents': 46, 'Notes': 34, 'ItemsJSON': 52,
    'VolumeTotalsJSON': 44, 'PaymentBreakdownJSON': 40, 'VoidReason': 34, 'Value': 40,
    'Note': 40, 'SentAt': 20, 'CreatedAt': 20, 'UpdatedAt': 20, 'BusinessDate': 14,
    'ReportDate': 14, 'GeneratedAt': 20, 'Key': 22, 'Category': 16, 'RoleName': 22,
}

MONEY = {'Price', 'Subtotal', 'DiscountValue', 'DiscountAmount', 'Total', 'AmountTendered',
         'ChangeDue', 'UnitPrice', 'LineTotal', 'PaidRevenue', 'UnpaidTotal', 'VoidedTotal',
         'AvgOrder'}
DATES = {'CreatedAt', 'UpdatedAt', 'LastLogin', 'BusinessDate', 'PaidAt', 'KitchenUpdatedAt',
         'ServedAt', 'VoidedAt', 'ReceivedAt', 'SentAt', 'GeneratedAt', 'ReportDate'}


# ----------------------------------------------------------------- helpers
def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def pwd_hash(password: str, salt: str) -> str:
    return 'sha256$' + sha256_hex(salt + '|' + password)


def json_compact(value) -> str:
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def style_header(ws, headers):
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
        c.alignment = CENTER
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(h, 16)
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = 'A2'


def write_rows(ws, headers, rows):
    """Append data rows then format only real cells (never inflate max_row)."""
    for r_i, row in enumerate(rows, start=2):
        for c_i, val in enumerate(row, start=1):
            cell = ws.cell(row=r_i, column=c_i, value=val)
            cell.font = BODY
            cell.border = BORDER
    for idx, h in enumerate(headers, start=1):
        col = get_column_letter(idx)
        fmt = '#,##0.00' if h in MONEY else ('yyyy-mm-dd hh:mm' if h in DATES else None)
        if fmt:
            for r in range(2, ws.max_row + 1):
                ws[f'{col}{r}'].number_format = fmt
    for r in range(2, ws.max_row + 1):
        if r % 2 == 0:
            for i in range(1, len(headers) + 1):
                ws.cell(row=r, column=i).fill = SOFT


# ----------------------------------------------------------------- sheets
def build_start_here(wb, spec):
    ws = wb.active
    ws.title = 'START HERE'
    ws.sheet_view.showGridLines = False
    ws.column_dimensions['A'].width = 4
    ws.column_dimensions['B'].width = 112

    ws['B2'] = spec['app']['title']
    ws['B2'].font = Font(bold=True, size=22, color=DARK)
    ws['B3'] = spec['app'].get('tagline', '') + ' - Google Sheets workbook'
    ws['B3'].font = Font(size=11, color='6F819A')

    token_hint = spec.get('token', {}).get('default', 'CHANGE-ME')
    steps = [
        ('1', 'Upload this file to Google Sheets',
         'drive.google.com > New > File upload > choose this .xlsx. Then right-click it in Drive > '
         'Open with > Google Sheets, and File > Save as Google Sheets.'),
        ('2', 'Open the Apps Script editor',
         'Extensions > Apps Script. Replace the default Code.gs and add Config.gs + Installer.gs '
         'from the google-apps-script folder of your download.'),
        ('3', 'Set your access token',
         'In Config.gs change ACCESS_TOKEN to a long random string you keep private. '
         'The generated project ships with: ' + token_hint),
        ('4', 'Run the installer',
         'Pick the function installKit and press Run. Approve the permissions. It creates every '
         'sheet, writes your products/branches/accounts/settings and protects password columns.'),
        ('5', 'Deploy as a Web App',
         'Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone. '
         'Copy the URL ending in /exec.'),
        ('6', 'Connect the app',
         'Open the app, press "Set up" on the sign-in screen, paste the /exec URL and the token, '
         'then Save & test.'),
        ('7', 'Sign in and pull',
         'Sign in as ' + spec['accounts']['ownerUsername'] + ' / ' + spec['accounts']['ownerPassword'] +
         ', open Settings and press "Pull menu & settings". Change the owner password immediately.'),
        ('8', 'Add your real catalogue',
         'Edit the Products sheet (keep the column names) or use Products & Menu inside the app. '
         'Then press "Pull menu & settings" again.'),
    ]

    r = 5
    for num, title, body in steps:
        c = ws.cell(row=r, column=1, value=num)
        c.font = Font(bold=True, size=14, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=ACCENT)
        c.alignment = CENTER
        ws.row_dimensions[r].height = 22
        ws.cell(row=r, column=2, value=title).font = Font(bold=True, size=12, color=DARK)
        b = ws.cell(row=r + 1, column=2, value=body)
        b.font = Font(size=10, color='3C4A5C')
        b.alignment = WRAP
        ws.row_dimensions[r + 1].height = 44
        r += 3

    ws.cell(row=r + 1, column=2, value=(
        'Passwords are never stored as plain text. Accounts keeps a salt and a SHA-256 hash only. '
        'Delete any TEST PASSWORDS block below before real use.')).font = Font(bold=True, size=10, color='B45309')
    ws.cell(row=r + 1, column=2).alignment = WRAP

    r += 3
    ws.cell(row=r, column=2, value='TEST PASSWORDS (delete before real use)').font = Font(bold=True, size=11, color=ACCENT)
    r += 1
    for i, h in enumerate(['Username', 'Test password', 'Role', 'Branch'], start=2):
        c = ws.cell(row=r, column=i, value=h)
        c.font = HEAD_FONT
        c.fill = HEAD_FILL
        c.alignment = CENTER
    for acc in spec['accounts']['list']:
        r += 1
        for i, v in enumerate([acc['username'], acc['password'], acc['roleId'], acc['branchId']], start=2):
            c = ws.cell(row=r, column=i, value=v)
            c.font = BODY
            c.border = BORDER

    r += 4
    ws.cell(row=r, column=2, value='WHAT EACH SHEET IS FOR').font = Font(bold=True, size=13, color=DARK)
    features = spec.get('features', {})
    guide = [
        ('Accounts', 'Sign-in rows: username, role, branch, status. Salt + SHA-256 hash only.'),
        ('Roles', 'What each role may do. The app reads this on every pull.'),
        ('Branches', 'Branch name, ID and the queue-card range.'),
        ('Products', 'The catalogue: price, category and VolumeJSON counts.'),
        ('Orders', 'One row per order. Written by the app.'),
        ('OrderItems', 'One row per item line. Written by the app.'),
        ('KitchenQueue', 'Every status change with time and staff (when the queue module is on).'),
        ('NonRevenueLogs', 'Non-sales counts: ' + ', '.join(t['label'] for t in spec.get('nonRevenueTypes', [])) or 'none'),
        ('Settings', 'App behaviour. Feature flags live here too: ' +
         ', '.join(k for k in ('FeatureKitchen', 'FeatureQueueCards', 'FeatureDiscounts',
                               'FeatureMultiBranch') if features.get(k.replace('Feature', '').lower(), True))),
        ('Reports', 'Daily totals per branch, written nightly.'),
        ('SyncLog', 'Trace of every send, login and void, with device and result.'),
    ]
    for name, desc in guide:
        r += 1
        cell = ws.cell(row=r, column=2, value=f'{name}  -  {desc}')
        cell.font = Font(size=10, color='3C4A5C')
        cell.alignment = WRAP
        ws.row_dimensions[r].height = 24


def build_accounts(wb, spec):
    ws = wb.create_sheet('Accounts')
    headers = SHEETS['Accounts']
    style_header(ws, headers)
    write_rows(ws, headers, seed_account_rows(spec))
    for col in ('G', 'H', 'I'):
        for r in range(2, ws.max_row + 1):
            ws[f'{col}{r}'].fill = LOCK

    role_ids = ','.join(r['id'] for r in spec['roles'])
    for col, formula in (('F', '"Active,Disabled"'), ('D', f'"{role_ids}"'), ('L', '"Yes,No"')):
        dv = DataValidation(type='list', formula1=formula, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f'{col}2:{col}500')


def build_roles(wb, spec):
    ws = wb.create_sheet('Roles')
    headers = SHEETS['Roles']
    style_header(ws, headers)
    rows = [[r['id'], r['name'], r.get('description', ''), ','.join(r['permissions']),
             r.get('isAdmin', 'No'), r.get('canPOS', 'Yes'), r.get('canKitchen', 'Yes'),
             r.get('allBranches', 'No'), r.get('canManage', 'No'), r.get('canExport', 'No')]
            for r in spec['roles']]
    write_rows(ws, headers, rows)
    for col in ('E', 'F', 'G', 'H', 'I', 'J'):
        dv = DataValidation(type='list', formula1='"Yes,No"', allow_blank=False)
        ws.add_data_validation(dv)
        dv.add(f'{col}2:{col}50')


def build_branches(wb, spec):
    ws = wb.create_sheet('Branches')
    headers = SHEETS['Branches']
    style_header(ws, headers)
    q = spec.get('queue', {})
    rows = [[b['id'], b['name'], b.get('address', ''), b.get('contact', ''),
             b.get('status', 'Active'), q.get('min', 1), q.get('max', 18), b.get('note', '')]
            for b in spec['branches']]
    write_rows(ws, headers, rows)
    dv = DataValidation(type='list', formula1='"Active,Closed"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add('E2:E200')


def build_products(wb, spec):
    ws = wb.create_sheet('Products')
    headers = SHEETS['Products']
    style_header(ws, headers)
    rows = []
    for i, p in enumerate(spec['products'], start=1):
        rows.append([p['id'], p['name'], p.get('category', ''), p.get('price', 0),
                     p.get('volumeLabel', ''), json_compact(p.get('volume', [])),
                     p.get('contents', ''), p.get('status', 'Active'),
                     p.get('sort', i * 10)])
    write_rows(ws, headers, rows)
    dv = DataValidation(type='list', formula1='"Active,Inactive"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add('H2:H1000')


def build_settings(wb, spec):
    ws = wb.create_sheet('Settings')
    headers = SHEETS['Settings']
    style_header(ws, headers)
    rows = [[k, str(v), spec.get('settingNotes', {}).get(k, '')]
            for k, v in spec['settings'].items()]
    write_rows(ws, headers, rows)
    ws.column_dimensions['A'].width = 24
    ws.column_dimensions['B'].width = 52
    ws.column_dimensions['C'].width = 56


def build_empty_sheet(wb, name):
    ws = wb.create_sheet(name)
    style_header(ws, SHEETS[name])
    return ws


# ----------------------------------------------------------------- main
def build(spec, out_path, describe_only=False):
    wb = Workbook()
    build_start_here(wb, spec)
    build_accounts(wb, spec)
    build_roles(wb, spec)
    build_branches(wb, spec)
    build_products(wb, spec)
    for name in ('Orders', 'OrderItems', 'KitchenQueue', 'NonRevenueLogs'):
        build_empty_sheet(wb, name)
    build_settings(wb, spec)
    for name in ('Reports', 'SyncLog'):
        build_empty_sheet(wb, name)

    for sheet in wb.worksheets:
        sheet.sheet_properties.tabColor = ACCENT if sheet.title in (
            'START HERE', 'Products', 'Settings') else DARK

    if describe_only:
        return {name: len(cols) for name, cols in SHEETS.items()}

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    wb.save(out_path)
    return {
        'path': out_path,
        'sheets': wb.sheetnames,
        'accounts': len(spec['accounts']['list']),
        'roles': len(spec['roles']),
        'branches': len(spec['branches']),
        'products': len(spec['products']),
        'settings': len(spec['settings']),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='tools-build-excel-template.py',
        description="Build the Google Sheets workbook for a generated project.")
    ap.add_argument('--spec', required=True, help='specification JSON file')
    ap.add_argument('--out', help='output .xlsx path (required unless --describe)')
    ap.add_argument('--describe', action='store_true',
                    help='print the sheet/column map and exit without writing')
    args = ap.parse_args(argv)

    try:
        with open(args.spec, encoding='utf-8') as f:
            spec = json.load(f)
    except FileNotFoundError:
        sys.stderr.write(f'error: spec not found: {args.spec}\n')
        return 2
    except json.JSONDecodeError as e:
        sys.stderr.write(f'error: spec is not valid JSON: {e}\n')
        return 2

    if args.describe:
        print(json.dumps({'sheets': list(SHEETS), 'columns': {k: len(v) for k, v in SHEETS.items()}},
                         indent=2))
        return 0

    if not args.out:
        sys.stderr.write('error: --out is required (or use --describe)\n')
        return 2

    result = build(spec, args.out)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
