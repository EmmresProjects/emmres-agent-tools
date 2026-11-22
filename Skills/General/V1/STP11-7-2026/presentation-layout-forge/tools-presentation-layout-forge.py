#!/usr/bin/env python3
"""CLI for inspecting, profiling, validating, and building editable PPTX layouts."""
from __future__ import annotations
import argparse, json, signal, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
import importlib.util
modspec=importlib.util.spec_from_file_location('layout_core', HERE/'tools-layout-core.py')
layout_core=importlib.util.module_from_spec(modspec); modspec.loader.exec_module(layout_core)
inspect_pptx=layout_core.inspect_pptx; profile_pptx=layout_core.profile_pptx; validate_pptx=layout_core.validate_pptx
load_json=layout_core.load_json; save_json=layout_core.save_json; DeckBuilder=layout_core.DeckBuilder

CAPABILITIES=[
 {'name':'inspect','description':'Extract slide, shape, table, and geometry details from a PPTX.'},
 {'name':'profile','description':'Learn reusable page, typography, margin, line, and table defaults from a PPTX.'},
 {'name':'validate','description':'Detect out-of-bounds objects, likely text overflow, overlaps, and connectors through shapes.'},
 {'name':'build','description':'Create editable title, section, bullet, table, flow, and UML slides from JSON.'},
 {'name':'options','description':'List Microsoft PowerPoint and Google Slides formatting, connector, arrow, shape, table, and text options.'},
 {'name':'capabilities','description':'List supported operations as structured JSON.'}
]

def fail(msg,code=2): print(json.dumps({'ok':False,'error':msg},ensure_ascii=False),file=sys.stderr); return code
def existing_file(value):
 p=Path(value)
 if not p.is_file(): raise argparse.ArgumentTypeError(f'file not found: {value}')
 return p

def parser():
 p=argparse.ArgumentParser(prog='tools-presentation-layout-forge.py',description='Create and inspect professional, editable PowerPoint layouts with orthogonal diagram routing.')
 p.add_argument('--json',action='store_true',help='Emit structured JSON to stdout.')
 sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('capabilities',help='List implemented capabilities.')
 q=sub.add_parser('options',help='List formatting and shape options.'); q.add_argument('--platform',choices=['all','powerpoint','google-slides'],default='all'); q.add_argument('--category'); q.add_argument('--output',type=Path)
 for name in ('inspect','profile','validate'):
  q=sub.add_parser(name,help=next(c['description'] for c in CAPABILITIES if c['name']==name)); q.add_argument('--input',required=True,type=existing_file); q.add_argument('--output',type=Path)
 q=sub.add_parser('build',help='Build an editable PPTX from a JSON specification.'); q.add_argument('--spec',required=True,type=existing_file); q.add_argument('--output',required=True,type=Path); q.add_argument('--preset',type=existing_file,default=HERE/'asset-style-preset.json'); q.add_argument('--template',type=existing_file); q.add_argument('--overwrite',action='store_true'); q.add_argument('--timeout',type=int,default=120)
 return p

def main(argv=None):
 args=parser().parse_args(argv)
 def timeout_handler(signum,frame): raise TimeoutError('operation timed out')
 try:
  if args.command=='capabilities': data={'ok':True,'capabilities':CAPABILITIES}
  elif args.command=='options':
   catalog=load_json(HERE/'asset-format-options-catalog.json'); shapes=load_json(HERE/'asset-shape-catalog.json')
   if args.platform=='powerpoint': selected={'powerpoint':catalog['powerpoint'],'production_defaults':catalog['production_defaults'],'shape_catalog':shapes}
   elif args.platform=='google-slides': selected={'google_slides':catalog['google_slides'],'production_defaults':catalog['production_defaults'],'shape_catalog':shapes}
   else: selected={**catalog,'shape_catalog':shapes}
   if args.category:
    key=args.category.strip().lower().replace('-','_')
    matches={}
    for scope,val in selected.items():
     if isinstance(val,dict) and key in val: matches[scope]={key:val[key]}
    if not matches: return fail(f'unknown option category: {args.category}')
    selected=matches
   data={'ok':True,'options':selected}
   if args.output: save_json(args.output,data)
  elif args.command in ('inspect','profile','validate'):
   fn={'inspect':inspect_pptx,'profile':profile_pptx,'validate':validate_pptx}[args.command]; data=fn(args.input); data['ok']=True
   if args.output: save_json(args.output,data)
  else:
   if args.output.exists() and not args.overwrite: return fail(f'output exists; use --overwrite: {args.output}')
   signal.signal(signal.SIGALRM,timeout_handler); signal.alarm(max(1,args.timeout))
   spec=load_json(args.spec); preset=load_json(args.preset)
   if not isinstance(spec.get('slides'),list) or not spec['slides']: return fail('spec.slides must be a non-empty array')
   supported={'title','section','bullets','table','flow','uml'}
   bad=[s.get('type') for s in spec['slides'] if s.get('type') not in supported]
   if bad:return fail(f'unsupported slide types: {bad}')
   args.output.parent.mkdir(parents=True,exist_ok=True)
   DeckBuilder(spec,preset,args.template).build().save(args.output); signal.alarm(0)
   report=validate_pptx(args.output); data={'ok':True,'output':str(args.output),'slide_count':len(spec['slides']),'validation':report}
  print(json.dumps(data,indent=2,ensure_ascii=False)); return 0
 except KeyboardInterrupt:return fail('cancelled by user',130)
 except TimeoutError as e:return fail(str(e),124)
 except Exception as e:return fail(f'{type(e).__name__}: {e}',1)
if __name__=='__main__': raise SystemExit(main())
