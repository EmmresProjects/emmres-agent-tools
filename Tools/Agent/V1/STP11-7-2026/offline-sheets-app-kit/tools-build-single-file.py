#!/usr/bin/env python3
"""
offline-sheets-app-kit - tools-build-single-file.py
===================================================
Copies a generated project's ``app/`` folder next to this tool and produces a
single self-contained HTML file with every stylesheet and script inlined.

Why this exists
---------------
Sandboxed previews (file viewers, chat clients, some LMS pages) block every
separate .css/.js request, and file:// pages cannot load sibling scripts from a
different origin. One inlined file always renders. It is also the easiest thing
to email or drop on a USB stick.

Usage
-----
    python tools-build-single-file.py --app ./build/cafe/app \
        --out ./build/cafe/app/cafe-pos.html

    # report what is on disk without writing
    python tools-build-single-file.py --app ./build/cafe/app --inspect

Dependencies: standard library only.
"""
import argparse
import base64
import json
import os
import re
import sys

BRAND_TOKEN = '{{BRAND_SLUG}}'   # replaced by the generator, kept for clarity

SCRIPT_TAG = re.compile(r'<script src="(js/[^"]+)"></script>\s*')
STYLE_TAG = re.compile(r'<link rel="stylesheet" href="([^"]+)">')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def inspect(app_dir):
    """Report the files the inliner will consume and the order it uses."""
    index = os.path.join(app_dir, 'index.html')
    if not os.path.isfile(index):
        return {'ok': False, 'error': f'no index.html in {app_dir}'}
    html = read(index)
    scripts = SCRIPT_TAG.findall(html)
    styles = STYLE_TAG.findall(html)
    missing = [p for p in scripts + styles if not os.path.isfile(os.path.join(app_dir, p))]
    return {
        'ok': not missing,
        'app': app_dir,
        'styles': styles,
        'scripts': scripts,
        'missing': missing,
    }


def build(app_dir, out_path):
    report = inspect(app_dir)
    if not report['ok']:
        return report

    html = read(os.path.join(app_dir, 'index.html'))

    # 1) inline stylesheets
    for rel in report['styles']:
        css = read(os.path.join(app_dir, rel))
        html = html.replace(f'<link rel="stylesheet" href="{rel}">',
                            '<style>\n' + css + '\n</style>')

    # 2) inline the favicon/SVG references as data URIs
    for m in set(re.findall(r'href="(img/[^"]+\.svg)"', html)):
        path = os.path.join(app_dir, m)
        if os.path.isfile(path):
            uri = 'data:image/svg+xml;base64,' + base64.b64encode(
                read(path).encode('utf-8')).decode('ascii')
            html = html.replace(f'href="{m}"', f'href="{uri}"')

    # 3) inline scripts in the order the tags appear
    bundle = []
    for rel in report['scripts']:
        body = read(os.path.join(app_dir, rel))
        bundle.append('\n/* ============ ' + rel + ' ============ */\n' + body)

    first = SCRIPT_TAG.search(html)
    if first is None:
        return {**report, 'ok': False, 'error': 'no <script src="js/..."> tags found'}

    html = SCRIPT_TAG.sub('', html)
    html = html[:first.start()] + '<script>\n' + ''.join(bundle) + '\n</script>\n' + html[first.start():]

    # 4) document the build and relax CSP for inline content
    html = html.replace('<title>', '<!-- single-file build: tools-build-single-file.py -->\n<title>', 1)
    html = html.replace("connect-src *;", "connect-src *; frame-src 'self' data:;")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or '.', exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    return {
        'ok': True,
        'path': out_path,
        'bytes': os.path.getsize(out_path),
        'scripts_inlined': len(report['scripts']),
        'styles_inlined': len(report['styles']),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='tools-build-single-file.py',
        description='Inline a generated app into one self-contained HTML file.')
    ap.add_argument('--app', required=True, help='the generated app/ folder')
    ap.add_argument('--out', help='output .html path (required unless --inspect)')
    ap.add_argument('--inspect', action='store_true',
                    help='list the assets that would be inlined and exit')
    args = ap.parse_args(argv)

    if not os.path.isdir(args.app):
        sys.stderr.write(f'error: app folder not found: {args.app}\n')
        return 2

    if args.inspect:
        print(json.dumps(inspect(args.app), indent=2))
        return 0

    if not args.out:
        sys.stderr.write('error: --out is required (or use --inspect)\n')
        return 2

    result = build(args.app, args.out)
    print(json.dumps(result, indent=2))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
