#!/usr/bin/env python3
"""
offline-sheets-app-kit - tools-make-icons.py
============================================
Generates every icon and splash image a generated project needs, from the
initial, colours and app name in the specification. No design tool required.

Outputs (under --out):
    app/img/icon.svg, icon-192.png, icon-512.png, favicon.png
    cordova/res/icon/android/{ldpi..xxxhdpi}.png
    cordova/res/icon/ios/*.png
    cordova/res/screen/android/{portrait,landscape}-*.png
    electron/build/icon.png, icon-256.png

Usage
-----
    python tools-make-icons.py --spec asset-spec-example-cafe.json --out ./build/cafe
    python tools-make-icons.py --spec spec.json --out ./build/x --only app

Dependencies: Pillow (pip install pillow>=10)
"""
import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    sys.stderr.write('Pillow is required:  pip install pillow>=10\n')
    sys.exit(3)

DARK_TOP = (16, 26, 38)
DARK_BOTTOM = (8, 12, 18)

ANDROID = {'ldpi': 36, 'mdpi': 48, 'hdpi': 72, 'xhdpi': 96, 'xxhdpi': 144, 'xxxhdpi': 192}
IOS = {'icon-60': 120, 'icon-76': 76, 'icon-120': 120, 'icon-152': 152,
       'icon-167': 167, 'icon-180': 180, 'icon-1024': 1024}
SCREENS = {
    'portrait': [(320, 480, 'ldpi'), (480, 800, 'mdpi'), (720, 1280, 'hdpi'),
                 (960, 1600, 'xhdpi'), (1280, 1920, 'xxhdpi')],
    'landscape': [(480, 320, 'ldpi'), (800, 480, 'mdpi'), (1280, 720, 'hdpi'),
                  (1600, 960, 'xhdpi'), (1920, 1280, 'xxhdpi')],
}


def hex_rgb(value, fallback):
    v = str(value or '').lstrip('#')
    if len(v) == 3:
        v = ''.join(c * 2 for c in v)
    if len(v) != 6:
        return fallback
    try:
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def find_bold_font():
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
              '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
              'C:/Windows/Fonts/arialbd.ttf',
              '/System/Library/Fonts/Supplemental/Arial Bold.ttf'):
        if os.path.exists(p):
            return p
    return None


def gradient(size, top=DARK_TOP, bottom=DARK_BOTTOM):
    img = Image.new('RGB', (size, size), bottom)
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        d.line([(0, y), (size, y)],
               fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


def make_icon(size, spec, rounded=True):
    ss = 4
    S = size * ss
    img = gradient(S).convert('RGBA')
    mask = Image.new('L', (S, S), 0)
    md = ImageDraw.Draw(mask)
    pad = int(S * 0.02)
    if rounded:
        md.rounded_rectangle([pad, pad, S - pad, S - pad], radius=int(S * 0.235), fill=255)
    else:
        md.rectangle([0, 0, S, S], fill=255)

    brand = hex_rgb(spec['app'].get('brandColor'), (255, 176, 46))
    brand2 = hex_rgb(spec['app'].get('brandColorAlt'), tuple(int(c * 0.82) for c in brand))
    ink = hex_rgb(spec['app'].get('brandInk'), (43, 27, 0))

    tile = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    for y in range(S):
        t = y / max(1, S - 1)
        td.line([(0, y), (S, y)],
                fill=tuple(int(brand[i] + (brand2[i] - brand[i]) * t) for i in range(3)) + (255,))

    initials = str(spec['app'].get('initials') or spec['app']['name'][:2]).upper()[:3]
    font_path = find_bold_font()
    if font_path:
        fs = int(S * (0.60 if len(initials) == 1 else 0.46))
        font = ImageFont.truetype(font_path, fs)
        bbox = td.textbbox((0, 0), initials, font=font)
        td.text(((S - (bbox[2] - bbox[0])) / 2 - bbox[0], (S - (bbox[3] - bbox[1])) / 2 - bbox[1]),
                initials, font=font, fill=ink + (255,))
    else:
        w = int(S * 0.14)
        td.rectangle([int(S * 0.33), int(S * 0.24), int(S * 0.33) + w, int(S * 0.76)], fill=ink)

    tile.putalpha(mask)
    return Image.alpha_composite(img, tile).resize((size, size), Image.LANCZOS)


def make_splash(w, h, spec):
    img = Image.new('RGB', (w, h), DARK_BOTTOM)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)],
               fill=tuple(int(DARK_TOP[i] + (DARK_BOTTOM[i] - DARK_TOP[i]) * t) for i in range(3)))
    mark = int(min(w, h) * 0.26)
    icon = make_icon(mark, spec)
    img.paste(icon, ((w - mark) // 2, int(h * 0.34)), icon)
    font_path = find_bold_font()
    if font_path:
        font = ImageFont.truetype(font_path, int(min(w, h) * 0.045))
        text = spec['app']['name']
        bbox = d.textbbox((0, 0), text, font=font)
        d.text(((w - (bbox[2] - bbox[0])) / 2, int(h * 0.34) + mark + int(min(w, h) * 0.05)),
               text, font=font, fill=(234, 242, 251))
    return img


def svg_icon(spec):
    brand = spec['app'].get('brandColor', '#ffb02e')
    brand2 = spec['app'].get('brandColorAlt', '#ff8a1f')
    ink = spec['app'].get('brandInk', '#2b1b00')
    initials = str(spec['app'].get('initials') or spec['app']['name'][:2]).upper()[:3]
    fs = 260 if len(initials) == 1 else 190
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <linearGradient id="b" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{brand}"/>
      <stop offset="1" stop-color="{brand2}"/>
    </linearGradient>
  </defs>
  <rect x="10" y="10" width="492" height="492" rx="120" fill="url(#b)"/>
  <text x="256" y="264" text-anchor="middle" dominant-baseline="central"
        font-family="Segoe UI, Roboto, Arial, sans-serif" font-weight="900"
        font-size="{fs}" fill="{ink}">{initials}</text>
</svg>
'''


def save(img, path, out_root):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, 'PNG', optimize=True)
    return os.path.relpath(path, out_root)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='tools-make-icons.py',
                                 description='Generate icons and splashes for a generated project.')
    ap.add_argument('--spec', required=True)
    ap.add_argument('--out', required=True, help='project output folder')
    ap.add_argument('--only', choices=['app', 'cordova', 'electron', 'all'], default='all')
    args = ap.parse_args(argv)

    try:
        spec = json.load(open(args.spec, encoding='utf-8'))
    except FileNotFoundError:
        sys.stderr.write(f'error: spec not found: {args.spec}\n')
        return 2
    except json.JSONDecodeError as e:
        sys.stderr.write(f'error: spec is not valid JSON: {e}\n')
        return 2

    root = os.path.abspath(args.out)
    written = []

    if args.only in ('app', 'all'):
        svg_path = os.path.join(root, 'app', 'img', 'icon.svg')
        os.makedirs(os.path.dirname(svg_path), exist_ok=True)
        with open(svg_path, 'w', encoding='utf-8') as f:
            f.write(svg_icon(spec))
        written.append(os.path.relpath(svg_path, root))
        for size, name in ((192, 'icon-192.png'), (512, 'icon-512.png'), (64, 'favicon.png')):
            written.append(save(make_icon(size, spec),
                                os.path.join(root, 'app', 'img', name), root))

    if args.only in ('cordova', 'all'):
        for density, size in ANDROID.items():
            written.append(save(make_icon(size, spec),
                                os.path.join(root, 'cordova', 'res', 'icon', 'android', f'{density}.png'), root))
        for name, size in IOS.items():
            written.append(save(make_icon(size, spec, rounded=size != 1024),
                                os.path.join(root, 'cordova', 'res', 'icon', 'ios', f'{name}.png'), root))
        for orient, items in SCREENS.items():
            for w, h, density in items:
                written.append(save(make_splash(w, h, spec),
                                    os.path.join(root, 'cordova', 'res', 'screen', 'android',
                                                 f'{orient}-{density}.png'), root))

    if args.only in ('electron', 'all'):
        written.append(save(make_icon(512, spec),
                            os.path.join(root, 'electron', 'build', 'icon.png'), root))
        written.append(save(make_icon(256, spec),
                            os.path.join(root, 'electron', 'build', 'icon-256.png'), root))

    print(json.dumps({'ok': True, 'written': len(written), 'files': written}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
