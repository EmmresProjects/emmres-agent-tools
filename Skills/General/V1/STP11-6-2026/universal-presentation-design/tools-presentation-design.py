#!/usr/bin/env python3
"""Primary entry point for the universal-presentation-design Skill Tool.

Run from the future skill root, for example:
  python3 universal-presentation-design/tools-presentation-design.py --help
  python3 universal-presentation-design/tools-presentation-design.py capabilities
"""
from __future__ import annotations
import argparse, importlib.util, json, sys, traceback
from pathlib import Path

TOOL_DIR=Path(__file__).resolve().parent

def _load(name,filename):
    spec=importlib.util.spec_from_file_location(name,TOOL_DIR/filename)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

def load_modules():
    names=['tools_core','tools_render','tools_raster','tools_pptx','tools_operations']
    if all(n in sys.modules for n in names):
        return tuple(sys.modules[n] for n in names)
    core=_load('tools_core','tools-core.py')
    try:
        render=_load('tools_render','tools-render.py')
        raster=_load('tools_raster','tools-raster.py')
        pptx=_load('tools_pptx','tools-pptx.py')
        ops=_load('tools_operations','tools-operations.py')
    except ImportError as exc:
        raise core.ToolError('DEPENDENCY_MISSING','A required Python package is missing.',{'error':str(exc),'install':'python3 -m pip install python-pptx==1.0.2 Pillow==11.3.0 jsonschema==4.25.1 segno==1.6.6'},core.EXIT_DEPENDENCY) from exc
    return core,render,raster,pptx,ops

def emit(data,exit_code=0,result_path=None,core=None):
    text=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
    if result_path and core:
        try: core.atomic_write(Path(result_path),text,overwrite=False,approved=False)
        except Exception: pass
    sys.stdout.write(text)
    return exit_code

def parse_params(value,core):
    if not value: return {}
    if value.startswith('@'):
        return core.load_json(core.safe_output_path(value[1:],must_exist=True),1024*1024)
    try:
        data=json.loads(value)
    except json.JSONDecodeError as exc:
        raise core.ToolError('INVALID_PARAMS','--params must be a JSON object or @path.json.',{'error':exc.msg})
    if not isinstance(data,dict): raise core.ToolError('INVALID_PARAMS','--params must be a JSON object.')
    return data

def parse_formats(value,core,config):
    if not value: return list(config['defaults']['formats'])
    formats=[f.strip().lower() for f in value.split(',') if f.strip()]
    bad=[f for f in formats if f not in {'json','svg','html','pptx','png','pdf','gif'}]
    if bad: raise core.ToolError('INVALID_FORMAT','Unsupported output format.',{'unsupported':bad})
    return list(dict.fromkeys(formats))

def add_common(p):
    p.add_argument('--config',help='Relative path to a configuration JSON file.')
    p.add_argument('--timeout',type=float,help='Cooperative time budget in seconds.')
    p.add_argument('--result',help='Optional relative path to write the structured JSON result.')
    p.add_argument('--approve',action='store_true',help='Approve a sensitive or destructive action.')
    p.add_argument('--overwrite',action='store_true',help='Allow overwriting an existing output.')

def add_input(p):
    p.add_argument('--input',help='Relative input path: JSON deck, Markdown, text, HTML, CSV/TSV, or PPTX.')
    p.add_argument('--text',help='Inline Markdown/text input.')
    p.add_argument('--kind',default='auto',choices=['auto','deck','markdown','text','html','csv','tsv','pptx'],help='Input kind override.')

def add_output(p,include_theme=True):
    p.add_argument('--output',required=True,help='Relative output directory.')
    p.add_argument('--formats',help='Comma-separated formats: json,svg,html,pptx,png,pdf,gif.')
    if include_theme: p.add_argument('--theme',help='Theme name or theme JSON path.')
    p.add_argument('--variant',default='standard',help='Layout variant: standard,thirds,golden,modular,asymmetric,symmetric,minimal,dense,focal,poster,magazine,comic,storyboard,handout.')
    p.add_argument('--params',help='JSON object or @path.json with capability parameters.')
    p.add_argument('--dry-run',action='store_true',help='Validate and plan without writing artifacts.')

def build_parser():
    p=argparse.ArgumentParser(prog='tools-presentation-design.py',description='Unified editable presentation design, production, accessibility, localization, QA, and handoff tool.',formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capabilities',help='List the operational capability registry.'); c.add_argument('--category',type=int); c.add_argument('--query'); c.add_argument('--family'); c.add_argument('--format',choices=['json','text'],default='json'); add_common(c)
    r=sub.add_parser('run',help='Run any registered capability by id.'); r.add_argument('--capability',required=True); add_input(r); add_output(r); add_common(r)
    b=sub.add_parser('build',help='Build editable deck artifacts from a deck/brief/data input.'); add_input(b); add_output(b); add_common(b)
    i=sub.add_parser('inspect',help='Inspect a deck JSON or PPTX file.'); add_input(i); i.add_argument('--output'); add_common(i)
    a=sub.add_parser('audit',help='Audit accessibility, brand, content, links, charts, privacy, motion, geometry, and media.'); add_input(a); a.add_argument('--output'); a.add_argument('--checks'); a.add_argument('--fail-on-major',action='store_true'); add_common(a)
    rp=sub.add_parser('repair',help='Apply safe accessibility/content repairs and write a repaired deck.'); add_input(rp); add_output(rp); add_common(rp)
    e=sub.add_parser('export',help='Export a deck to selected formats.'); add_input(e); add_output(e); add_common(e)
    pr=sub.add_parser('parse-request',help='Extract categories/skills from plain text, Markdown, or copied HTML.'); pr.add_argument('--input'); pr.add_argument('--text'); pr.add_argument('--output'); add_common(pr)
    ba=sub.add_parser('batch',help='Run a bounded batch from a JSON manifest.'); ba.add_argument('--manifest',required=True); ba.add_argument('--report'); add_common(ba)
    d=sub.add_parser('diff',help='Compare two deck JSON files.'); d.add_argument('--before',required=True); d.add_argument('--after',required=True); d.add_argument('--output'); add_common(d)
    m=sub.add_parser('merge',help='Merge multiple deck/brief/data inputs into one deck.'); m.add_argument('--inputs',required=True,help='Comma-separated relative input paths.'); add_output(m); add_common(m)
    l=sub.add_parser('localize',help='Apply supplied translations and produce localization artifacts.'); add_input(l); l.add_argument('--locale',default='fr'); l.add_argument('--translations'); add_output(l); add_common(l)
    t=sub.add_parser('theme',help='Apply or report a theme.'); add_input(t); add_output(t,include_theme=False); t.add_argument('--theme',required=True,help='Theme name or theme JSON path.'); add_common(t)
    s=sub.add_parser('schema',help='Print the deck JSON schema.'); s.add_argument('--output'); add_common(s)
    st=sub.add_parser('self-test',help='Run the bundled test suite.'); st.add_argument('--output'); st.add_argument('--quick',action='store_true'); add_common(st)
    return p

def main(argv=None):
    parser=build_parser(); args=parser.parse_args(argv)
    core=None
    try:
        core,render,raster,pptx,ops=load_modules()
        config=core.load_config(args.config)
        if args.timeout is not None: config['defaults']['timeout_seconds']=args.timeout
        config['_overwrite']=bool(getattr(args,'overwrite',False)); config['_approved']=bool(getattr(args,'approve',False))
        themes=core.load_themes(config)
        schema=core.load_schema(config)
        timeout=getattr(args,'timeout',None) or config['defaults']['timeout_seconds']
        with core.Deadline(timeout) as deadline:
            cmd=args.command
            if cmd=='capabilities':
                reg,caps=ops.load_registry(config); items=ops.list_capabilities(reg,category=args.category,query=args.query,family=args.family)
                if args.format=='text':
                    for c in items: print(f"{c['id']}\t{c['family']}\t{c['name']}")
                    return emit({'ok':True,'count':len(items)},0,args.result,core)
                return emit({'ok':True,'count':len(items),'capability_count':reg['capability_count'],'category_count':reg['category_count'],'capabilities':items},0,args.result,core)
            if cmd=='schema':
                if args.output: core.atomic_write(core.safe_output_path(args.output),json.dumps(schema,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                return emit({'ok':True,'schema':schema},0,args.result,core)
            if cmd=='parse-request':
                if args.text is not None: source=args.text
                elif args.input: source=core.read_text_input(core.safe_output_path(args.input,must_exist=True),config)
                else: raise core.ToolError('INPUT_REQUIRED','Provide --input or --text.')
                data=core.parse_request(source)
                if args.output: core.atomic_write(core.safe_output_path(args.output),json.dumps(data,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                return emit({'ok':True,'data':data},0,args.result,core)
            if cmd=='diff':
                before=core.load_json(core.safe_output_path(args.before,must_exist=True),config['limits']['max_input_bytes'])
                after=core.load_json(core.safe_output_path(args.after,must_exist=True),config['limits']['max_input_bytes'])
                data=core.diff_decks(before,after)
                if args.output: core.atomic_write(core.safe_output_path(args.output),json.dumps(data,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                return emit({'ok':True,'data':data},0,args.result,core)
            if cmd=='self-test':
                tests=_load('tools_tests','tools-tests.py')
                report=tests.run_tests(quick=args.quick)
                if args.output: core.atomic_write(core.safe_output_path(args.output),json.dumps(report,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                return emit(report,0 if report.get('ok') else core.EXIT_INTERNAL,args.result,core)
            if cmd=='batch':
                manifest=core.load_json(core.safe_output_path(args.manifest,must_exist=True),config['limits']['max_input_bytes'])
                items=manifest.get('items') if isinstance(manifest,dict) else manifest
                if not isinstance(items,list) or not items: raise core.ToolError('INVALID_MANIFEST','Batch manifest must contain a non-empty items array.')
                if len(items)>config['limits']['max_batch_items']: raise core.ToolError('BATCH_TOO_LARGE','Batch exceeds item limit.',{'limit':config['limits']['max_batch_items']})
                reg,caps=ops.load_registry(config); results=[]
                for idx,item in enumerate(items,1):
                    deadline.check(f'batch item {idx}')
                    cap=item.get('capability','powerpoint-deck-builder')
                    if cap not in caps: raise core.ToolError('UNKNOWN_CAPABILITY','Unknown capability in batch.',{'capability':cap})
                    deck,kind=core.read_deck_input(item.get('input'),config,kind=item.get('kind','auto'))
                    outdir=core.prepare_output_dir(core.safe_output_path(item['output']),overwrite=args.overwrite,approved=args.approve)
                    ctx={'deck':deck,'input_path':item.get('input'),'input_kind':kind,'outdir':outdir,'params':item.get('params',{}),'config':config,'themes':themes,'schema':schema,'approve':args.approve or item.get('approve',False),'formats':parse_formats(item.get('formats'),core,config),'variant':item.get('variant','standard'),'deadline':deadline}
                    results.append(ops.run_capability(caps[cap],ctx))
                report={'ok':True,'count':len(results),'results':results}
                if args.report: core.atomic_write(core.safe_output_path(args.report),json.dumps(report,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                return emit(report,0,args.result,core)
            # Input-driven commands
            if cmd in {'run','build','repair','export','localize','theme','merge','inspect','audit'}:
                if cmd=='merge':
                    paths=[x.strip() for x in args.inputs.split(',') if x.strip()]
                    if not paths: raise core.ToolError('INPUT_REQUIRED','Provide at least one input to merge.')
                    merged=None
                    for path in paths:
                        deck,kind=core.read_deck_input(path,config)
                        if merged is None: merged=deck
                        else:
                            existing={s['id'] for s in merged['slides']}
                            for s in deck['slides']:
                                ns=dict(s); base=ns['id']; n=2
                                while ns['id'] in existing: ns['id']=f'{base}-{n}'; n+=1
                                existing.add(ns['id']); merged['slides'].append(ns)
                    deck=merged; kind='merge'; input_path=paths[0]
                else:
                    input_path=args.input
                    try:
                        deck,kind=core.read_deck_input(args.input,config,kind=args.kind,text=args.text)
                    except core.ToolError as exc:
                        if cmd=='run' and args.capability=='presentation-recovery-and-corruption-specialist' and exc.code=='PPTX_READ_FAILED':
                            kind='pptx'; deck={'schema_version':'1.0','title':'Recovery input','slides':[{'id':'recovery-input','layout':'bullets','title':'Recovery input','body':'The source PPTX will be salvaged by the recovery handler.'}]}
                        else:
                            raise
                core.validate_deck(deck,config,schema)
                theme_arg=getattr(args,'theme',None)
                if theme_arg and theme_arg.endswith('.json'):
                    custom=core.load_json(core.safe_output_path(theme_arg,must_exist=True),1024*1024)
                    themes=dict(themes); themes['custom']=custom; deck['theme']='custom'
                elif theme_arg: deck['theme']=theme_arg
                if cmd in {'run','build','export','localize','theme','repair','merge'}:
                    outdir=core.prepare_output_dir(core.safe_output_path(args.output),overwrite=args.overwrite,approved=args.approve)
                    params=parse_params(getattr(args,'params',None),core)
                    formats=parse_formats(getattr(args,'formats',None),core,config)
                    if cmd=='run':
                        reg,caps=ops.load_registry(config)
                        if args.capability not in caps: raise core.ToolError('UNKNOWN_CAPABILITY','Unknown capability id.',{'capability':args.capability,'hint':'Run capabilities --query <term>'},core.EXIT_NOT_FOUND)
                        entry=caps[args.capability]
                    elif cmd in {'build','merge'}: entry=ops.load_registry(config)[1]['powerpoint-deck-builder']
                    elif cmd=='export': entry={'id':'export','name':'Export','family':'pptx','preset':'powerpoint-export-specialist','mode':'local','approval_required':False}
                    elif cmd=='localize':
                        entry={'id':'localize','name':'Localize','family':'localization','preset':'presentation-localization-manager','mode':'local','approval_required':False}
                        params['locale']=args.locale
                        if args.translations: params['translations']=core.load_json(core.safe_output_path(args.translations,must_exist=True),1024*1024)
                    elif cmd=='theme':
                        entry={'id':'theme','name':'Theme','family':'theme','preset':'brand-theme-translator','mode':'local','approval_required':False}; params['theme']=args.theme
                    else:
                        entry={'id':'repair','name':'Repair','family':'accessibility','preset':'accessibility-remediation-specialist','mode':'local','approval_required':False}
                    if args.dry_run:
                        return emit({'ok':True,'dry_run':True,'capability':entry['id'],'slide_count':len(deck['slides']),'formats':formats,'output':args.output,'approval_required':entry.get('approval_required',False)},0,args.result,core)
                    ctx={'deck':deck,'input_path':input_path,'input_kind':kind,'outdir':outdir,'params':params,'config':config,'themes':themes,'schema':schema,'approve':args.approve,'formats':formats,'variant':args.variant,'deadline':deadline}
                    result=ops.run_capability(entry,ctx)
                    return emit(result,0,args.result,core)
            if cmd=='inspect':
                if kind=='pptx': data=pptx.inspect_pptx(core.safe_output_path(args.input,must_exist=True),config)
                else:
                    core.validate_deck(deck,config,schema); data={'deck_title':deck.get('title'),'slide_count':len(deck['slides']),'slides':[{'id':s['id'],'layout':s['layout'],'title':s.get('title'),'words':len(core.slide_text(s).split())} for s in deck['slides']],'assets':deck.get('assets',[])}
                if args.output: core.atomic_write(core.safe_output_path(args.output),json.dumps(data,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                return emit({'ok':True,'data':data},0,args.result,core)
            if cmd=='audit':
                checks=args.checks.split(',') if args.checks else None
                data=core.audit_deck(deck,config,themes,checks=checks)
                if args.output: core.atomic_write(core.safe_output_path(args.output),json.dumps(data,indent=2)+'\n',overwrite=args.overwrite,approved=args.approve)
                exit_code=core.EXIT_VALIDATION if args.fail_on_major and not data['passed'] else 0
                return emit({'ok':exit_code==0,'data':data},exit_code,args.result,core)
            raise core.ToolError('UNKNOWN_COMMAND','Unsupported command.',{'command':cmd},core.EXIT_INTERNAL)
    except Exception as exc:
        if core is not None and isinstance(exc,core.ToolError):
            return emit({'ok':False,'error':exc.to_dict()},exc.exit_code,getattr(args,'result',None),core)
        return emit({'ok':False,'error':{'code':'INTERNAL_ERROR','message':str(exc),'details':{'traceback':traceback.format_exc(limit=5)}}},1,getattr(args,'result',None),core)

if __name__=='__main__':
    raise SystemExit(main())
