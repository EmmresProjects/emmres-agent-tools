#!/usr/bin/env python3
"""Primary CLI for the unified Environment, Workspace, Sandbox and File Management tool."""
from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import importlib.util
import json
import re
import sys
from pathlib import Path

# The component is copied beside a parent SKILL.md; running it must not create package subfolders.
sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
MODULES = {}
for _part in ('core','inspect','files','system','assess','runtime','workflow','domain'):
    _key = 'workspace_' + _part
    _spec = importlib.util.spec_from_file_location(_key, BASE / ('tools-' + _part + '.py'))
    if _spec is None or _spec.loader is None:
        raise RuntimeError('Missing required sibling tool module: ' + _part)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_key] = _module
    _spec.loader.exec_module(_module)
    MODULES[_part] = _module

from workspace_core import Context, ToolError, load_json, public_data, redact, utc

CATALOG = load_json(BASE / 'asset-capabilities.json', 2 * 1024 * 1024)
if len(CATALOG['categories']) != 31 or len(CATALOG['capabilities']) != 310:
    raise RuntimeError('Capability registry is incomplete')
REGISTRY = {cap['id']:cap for cap in CATALOG['capabilities']}
NUMBERS = {cap['number']:cap for cap in CATALOG['capabilities']}
if len(REGISTRY) != 310 or len(NUMBERS) != 310:
    raise RuntimeError('Duplicate capability ID or number')


def dispatch(ctx: Context, route: str) -> dict:
    group, sep, variant = route.partition('.')
    if not sep or group not in MODULES or group in ('core',):
        raise ToolError('internal_error','Unknown capability route',route=route)
    value=MODULES[group].run(ctx,variant)
    if not isinstance(value,dict):
        raise ToolError('internal_error','Capability did not return a structured object',route=route)
    return value


MODULES['workflow'].configure(dispatch,REGISTRY)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError('invalid_arguments',message)


def _parser() -> Parser:
    p=Parser(prog='python3 environment-workspace-sandbox-management/tools-workspace.py',
             description='One unified, evidence-based workspace control tool for 31 categories and 310 catalog capabilities.',
             formatter_class=argparse.RawDescriptionHelpFormatter,
             epilog=('Examples (from a future skill root):\n'
                     '  python3 environment-workspace-sandbox-management/tools-workspace.py capabilities --category 1\n'
                     '  python3 environment-workspace-sandbox-management/tools-workspace.py run workspace-inventory-scanner --root .\n'
                     '  python3 environment-workspace-sandbox-management/tools-workspace.py run batch-move-and-copy-operator --root . --input reports/transfer.json\n'
                     '  python3 environment-workspace-sandbox-management/tools-workspace.py run batch-move-and-copy-operator --root . --input reports/transfer.json --apply --approve TOKEN\n'
                     '  python3 environment-workspace-sandbox-management/tools-selftest.py'))
    sub=p.add_subparsers(dest='command',required=True)
    cap=sub.add_parser('capabilities',help='List all capabilities or a category; --json for machine-readable data')
    cap.add_argument('--category',help='Category number, exact name, or lowercase category slug')
    cap.add_argument('--json',action='store_true',help='Output structured JSON')
    run=sub.add_parser('run',help='Run a catalog capability by slug or number (e.g. 1.1)')
    run.add_argument('capability',help='Exact capability slug or catalog number')
    run.add_argument('--root',default='.',help='Existing authorized workspace root (default: current directory)')
    run.add_argument('--input',help='Root-relative JSON object with operation parameters')
    run.add_argument('--output',help='Root-relative new JSON report file; stdout remains structured')
    run.add_argument('--policy',help='Root-relative override of the bundled policy JSON')
    run.add_argument('--apply',action='store_true',help='Execute a reviewed mutation')
    run.add_argument('--approve',help='Exact approval token from a fresh preflight (not an authentication credential)')
    resolve=sub.add_parser('resolve',help='Resolve names/categories in plain text, Markdown, or copied HTML to the 310 registry entries')
    resolve.add_argument('--root',default='.',help='Existing root for reading --source')
    source=resolve.add_mutually_exclusive_group(required=True)
    source.add_argument('--source',help='Root-relative UTF-8 text/HTML/Markdown file')
    source.add_argument('--text',help='Inline text/HTML/Markdown')
    sub.add_parser('selftest',help='Run the bundled temporary-workspace integration tests')
    return p


def _normalize(text: str) -> str:
    return re.sub(r'[^a-z0-9]+',' ',html.unescape(text).lower()).strip()


class _Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[]
    def handle_data(self,data):self.parts.append(data)
    def handle_starttag(self,tag,attrs):
        if tag in ('p','div','br','li','tr','h1','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('p','div','li','tr','h1','h2','h3'):self.parts.append('\n')


def _resolve(text: str) -> dict:
    if len(text)>262144:
        raise ToolError('limit_exceeded','Resolve source exceeds 256 KiB')
    parser=_Plain();parser.feed(text)
    lines=[_normalize(re.sub(r'^(?:\s*[#*>-]+\s*|\s*\d+[.)]\s*)','',line)) for line in ''.join(parser.parts).splitlines()]
    lines=[x for x in lines if x]
    selected=set();matched_categories=[];matched_names=[]
    for line in lines:
        line=re.sub(r'^(?:category\s+\d+\s*|skill name\s*|description\s*)','',line).strip()
        for cat in CATALOG['categories']:
            if line==_normalize(cat['name']) or line==str(cat['number'])+' '+_normalize(cat['name']):
                matched_categories.append(cat['name'])
                selected.update(x['id'] for x in CATALOG['capabilities'] if x['category']==cat['number'])
        for cap in CATALOG['capabilities']:
            if line==_normalize(cap['name']) or line==_normalize(cap['number']+' '+cap['name']):
                matched_names.append(cap['name']);selected.add(cap['id'])
    return {'categories':sorted(set(matched_categories)),'names':sorted(set(matched_names)),
            'capabilities':[x for x in CATALOG['capabilities'] if x['id'] in selected],
            'count':len(selected),'note':'Only exact catalog names and categories are resolved; no new capability is invented.'}


def _run(args) -> dict:
    cap=REGISTRY.get(args.capability) or NUMBERS.get(args.capability)
    if cap is None:raise ToolError('unknown_capability','Unknown capability slug or catalog number',capability=args.capability)
    if args.approve and not args.apply:raise ToolError('invalid_arguments','--approve requires --apply')
    if args.apply and cap['mode']!='action':
        raise ToolError('not_applicable','This capability is read-only or advisory; remove --apply',capability=cap['id'])
    default=load_json(BASE/'asset-policy.json')
    preliminary=Context(args.root,default,cap,{})
    policy=load_json(preliminary.path(args.policy,must_exist=True),default['limits']['max_input_bytes']) if args.policy else default
    req=load_json(preliminary.path(args.input,must_exist=True),default['limits']['max_input_bytes']) if args.input else {}
    ctx=Context(args.root,policy,cap,req,apply=args.apply,approve=args.approve)
    result=dispatch(ctx,cap['route'])
    payload={'ok':result.get('status') not in ('failed','cancelled'),'time':utc(),
             'capability':{'id':cap['id'],'number':cap['number'],'name':cap['name'],
                           'category':cap['category'],'category_name':CATALOG['categories'][cap['category']-1]['name'],
                           'mode':cap['mode'],'route':cap['route']},
             'status':result.get('status','inspected'),'result':result}
    if args.output:payload['output_file']=ctx.json_output(args.output,payload)
    return payload


def main(argv=None) -> int:
    try:
        args=_parser().parse_args(argv)
        if args.command=='capabilities':
            categories=CATALOG['categories'];caps=CATALOG['capabilities']
            if args.category:
                cat=next((x for x in categories if args.category in (str(x['number']),x['name'],x['slug'])),None)
                if cat is None:raise ToolError('invalid_input','Unknown category',category=args.category)
                categories=[cat];caps=[x for x in caps if x['category']==cat['number']]
            if args.json:
                print(json.dumps({'ok':True,'categories':categories,'count':len(caps),'capabilities':caps},ensure_ascii=False,indent=2))
            else:
                print('Environment, Workspace, Sandbox, and File Management — '+str(len(caps))+' capabilities')
                for cat in categories:
                    print('\nCategory '+str(cat['number'])+': '+cat['name'])
                    for c in caps:
                        if c['category']==cat['number']:
                            print('  '+c['number']+'  '+c['id']+'  ['+c['mode']+']  '+c['name'])
            return 0
        if args.command=='run':
            data=_run(args)
            print(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
            return 0 if data['ok'] else 4
        if args.command=='resolve':
            cap=REGISTRY['workspace-inventory-scanner']
            ctx=Context(args.root,load_json(BASE/'asset-policy.json'),cap,{})
            text=ctx.read_text(args.source,max_bytes=262144) if args.source else args.text
            print(json.dumps({'ok':True,**_resolve(text)},ensure_ascii=False,indent=2))
            return 0
        if args.command=='selftest':
            import subprocess
            result=subprocess.run([sys.executable,str(BASE/'tools-selftest.py')],check=False)
            return result.returncode
    except ToolError as exc:
        print(json.dumps({'ok':False,'error':{'code':exc.code,'message':redact(exc.message),
                                             'details':public_data(exc.details)}},ensure_ascii=False,indent=2))
        return 130 if exc.code=='cancelled' else 3 if exc.code in ('integration_unavailable','not_allowed') else 2
    except KeyboardInterrupt:
        print(json.dumps({'ok':False,'error':{'code':'cancelled',
              'message':'Interrupted. Inspect any transaction journal or workflow checkpoint before resuming.'}}))
        return 130
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'ok':False,'error':{'code':'internal_error','message':redact(str(exc))[:300]}},ensure_ascii=False))
        return 4
    return 4


if __name__=='__main__':
    sys.exit(main())
