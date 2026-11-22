"""Bundled validation suite. Run via the primary CLI: self-test [--quick].

Tests use the real CLI interface, temporary relative output paths, and a clean
temporary skill root containing one test SKILL.md beside the tool folder.
"""
from __future__ import annotations
import contextlib, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

TOOL_DIR=Path(__file__).resolve().parent
TOOL_NAME=TOOL_DIR.name
ENTRY=f'{TOOL_NAME}/tools-presentation-design.py'

def _cli_module():
    spec=importlib.util.spec_from_file_location('upd_cli',TOOL_DIR/'tools-presentation-design.py')
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def _run_cli(cli,args):
    buf=io.StringIO()
    with contextlib.redirect_stdout(buf):
        code=cli.main(args)
    try: data=json.loads(buf.getvalue())
    except json.JSONDecodeError: data={'raw':buf.getvalue()}
    return code,data

def run_tests(quick=False):
    cli=_cli_module(); core,render,raster,pptx,ops=cli.load_modules()
    config=core.load_config(None); reg,caps=ops.load_registry(config)
    example=f'{TOOL_NAME}/asset-example-deck.json'
    results=[]
    root=Path.cwd()/f'.upd-test-{os.getpid()}'
    root.mkdir(exist_ok=True)
    def out(name): return str((root/name).relative_to(Path.cwd()))
    def record(name,fn):
        try:
            details=fn(); results.append({'name':name,'ok':True,'details':details or {}})
        except Exception as exc:
            results.append({'name':name,'ok':False,'details':{'error':str(exc)}})
    try:
        def help_caps():
            r=subprocess.run([sys.executable,ENTRY,'--help'],capture_output=True,text=True,timeout=30)
            assert r.returncode==0 and 'capabilities' in r.stdout
            code,data=_run_cli(cli,['capabilities','--format','json'])
            assert code==0 and data['capability_count']==525
            return {'capability_count':data['capability_count']}
        record('help_and_capabilities',help_caps)
        def schema():
            import jsonschema
            code,data=_run_cli(cli,['schema'])
            assert code==0; jsonschema.Draft202012Validator.check_schema(data['schema'])
            return {'schema':'valid'}
        record('schema',schema)
        def build_all():
            code,data=_run_cli(cli,['build','--input',example,'--output',out('build'),'--formats','json,svg,html,pptx,png,pdf,gif'])
            assert code==0 and data['ok'], data
            arts=data['artifacts']; assert any(a.endswith('.pptx') for a in arts) and any(a.endswith('.pdf') for a in arts) and any(a.endswith('.gif') for a in arts)
            from pptx import Presentation
            ppt=next(a for a in arts if a.endswith('.pptx'))
            prs=Presentation(ppt); assert len(prs.slides)==8
            with zipfile.ZipFile(ppt) as z: assert z.testzip() is None
            from lxml import etree
            svg=next(a for a in arts if a.endswith('.svg')); etree.fromstring(Path(svg).read_bytes())
            html=next(a for a in arts if a.endswith('.html')); assert '<!doctype html>' in Path(html).read_text()
            pdf=next(a for a in arts if a.endswith('.pdf')); b=Path(pdf).read_bytes(); assert b.startswith(b'%PDF') and b'%%EOF' in b[-2048:]
            from PIL import Image
            gif=next(a for a in arts if a.endswith('.gif')); assert Image.open(gif).n_frames==8
            return {'artifacts':len(arts),'slides':len(prs.slides)}
        record('build_all_formats',build_all)
        def inputs():
            fps=[]
            for name,inp,kind in [('md',f'{TOOL_NAME}/asset-example-brief.md','markdown'),('html',f'{TOOL_NAME}/asset-example-request.html','html'),('csv',f'{TOOL_NAME}/asset-example-data.csv','csv')]:
                code,data=_run_cli(cli,['build','--input',inp,'--kind',kind,'--output',out('in-'+name),'--formats','json'])
                assert code==0 and data['ok'], data
                deck=json.loads(Path(data['artifacts'][0]).read_text()); fps.append(core.fingerprint(deck))
            assert len(set(fps))==3
            return {'distinct_fingerprints':len(set(fps))}
        record('markdown_html_csv_inputs',inputs)
        def parse_req():
            code,data=_run_cli(cli,['parse-request','--input',f'{TOOL_NAME}/asset-example-request.html'])
            assert code==0 and data['data']['skill_count']>=3
            return {'skills':data['data']['skill_count']}
        record('parse_request_html',parse_req)
        def invalid():
            bad=root/'bad.json'; bad.write_text('{bad')
            code,data=_run_cli(cli,['build','--input',str(bad.relative_to(Path.cwd())),'--output',out('bad-out'),'--formats','json'])
            assert code==2 and data['error']['code']=='INVALID_JSON'
            code,data=_run_cli(cli,['build','--input','missing-file.json','--output',out('missing-out'),'--formats','json'])
            assert code==4
            code,data=_run_cli(cli,['build','--input',example,'--output','../escape','--formats','json'])
            assert code==2 and data['error']['code']=='UNSAFE_PATH'
            code,data=_run_cli(cli,['run','--capability','not-a-capability','--input',example,'--output',out('unknown'),'--formats','json'])
            assert code==4
            return {'invalid_json':True,'missing_input':True,'unsafe_path':True,'unknown_capability':True}
        record('invalid_and_missing_inputs',invalid)
        def approval():
            code,data=_run_cli(cli,['run','--capability','google-slides-publishing-specialist','--input',example,'--output',out('publish'),'--formats','json'])
            assert code==3 and data['error']['code']=='APPROVAL_REQUIRED'
            code,data=_run_cli(cli,['build','--input',example,'--output',out('build'),'--formats','json'])
            assert code==3
            code,data=_run_cli(cli,['build','--input',example,'--output',out('build'),'--formats','json','--overwrite','--approve'])
            assert code==0 and (root/'build.bak').exists()
            return {'approval_gate':True,'overwrite_backup':True}
        record('approval_and_overwrite_recovery',approval)
        def audit_repair():
            code,data=_run_cli(cli,['audit','--input',example,'--fail-on-major'])
            assert code==2 and not data['data']['passed']
            code,data=_run_cli(cli,['repair','--input',example,'--output',out('repaired'),'--formats','json'])
            assert code==0
            repaired=next(a for a in data['artifacts'] if Path(a).name=='deck.json')
            code,data=_run_cli(cli,['audit','--input',repaired,'--fail-on-major'])
            assert code==0 and data['data']['passed'], data['data']['findings']
            return {'repair_changes':len(json.loads(Path(repaired).read_text()).get('review',{}).get('changes',[]))}
        record('audit_and_repair',audit_repair)
        def localize():
            code,data=_run_cli(cli,['localize','--input',example,'--locale','fr','--translations',f'{TOOL_NAME}/asset-example-translations.json','--output',out('loc'),'--formats','json'])
            assert code==0 and data['data']['applied']>0 and data['data']['locale']=='fr'
            return {'applied':data['data']['applied']}
        record('localization',localize)
        def pptx_recover():
            ppt=next(a for a in json.loads(json.dumps(_run_cli(cli,['build','--input',example,'--output',out('ppt-src'),'--formats','pptx'])[1]))['artifacts'] if a.endswith('.pptx'))
            code,data=_run_cli(cli,['inspect','--input',ppt])
            assert code==0 and data['data']['slide_count']==8
            corrupt=root/'corrupt.pptx'
            with zipfile.ZipFile(ppt) as zin, zipfile.ZipFile(corrupt,'w') as zout:
                for item in zin.infolist():
                    zout.writestr(item,b'<broken' if item.filename=='ppt/slides/slide1.xml' else zin.read(item.filename))
            code,data=_run_cli(cli,['run','--capability','presentation-recovery-and-corruption-specialist','--input',str(corrupt.relative_to(Path.cwd())),'--kind','pptx','--output',out('recover'),'--formats','json'])
            assert code==0 and data['ok'], data
            return {'recovered':True}
        record('pptx_inspect_and_recovery',pptx_recover)
        def batch_diff_merge():
            manifest=root/'manifest.json'; manifest.write_text(json.dumps({'items':[{'input':example,'output':out('batch-a'),'capability':'powerpoint-deck-builder','formats':'json'},{'input':example,'output':out('batch-b'),'capability':'chart-selection-advisor','formats':'json'}]}))
            code,data=_run_cli(cli,['batch','--manifest',str(manifest.relative_to(Path.cwd()))])
            assert code==0 and data['count']==2
            a=json.loads(Path(data['results'][0]['artifacts'][0]).read_text()); b=json.loads(Path(data['results'][1]['artifacts'][0]).read_text())
            before=root/'before.json'; after=root/'after.json'; before.write_text(json.dumps(a)); after.write_text(json.dumps(b))
            code,data=_run_cli(cli,['diff','--before',str(before.relative_to(Path.cwd())),'--after',str(after.relative_to(Path.cwd()))])
            assert code==0
            code,data=_run_cli(cli,['merge','--inputs',f'{example},{TOOL_NAME}/asset-example-brief.md','--output',out('merged'),'--formats','json'])
            assert code==0
            return {'batch':2,'diff':True,'merge':True}
        record('batch_diff_merge',batch_diff_merge)
        def all_caps():
            items=list(caps.items())
            if quick:
                seen=set(); items=[(k,v) for k,v in items if v['family'] not in seen and not seen.add(v['family'])]
            failures=[]
            for cid,entry in items:
                code,data=_run_cli(cli,['run','--capability',cid,'--input',example,'--output',out('cap-'+cid),'--formats','json','--approve','--timeout','30'])
                if code!=0 or not data.get('ok'): failures.append({'id':cid,'code':code,'error':data.get('error')})
            assert not failures, failures[:5]
            return {'dispatched':len(items),'failures':0}
        record('all_capabilities' if not quick else 'family_representatives',all_caps)
        def family_full():
            seen=set(); reps=[]
            for cid,entry in caps.items():
                if entry['family'] not in seen:
                    seen.add(entry['family']); reps.append(cid)
            failures=[]; artifacts=0
            for cid in reps:
                code,data=_run_cli(cli,['run','--capability',cid,'--input',example,'--output',out('fam-'+cid),'--formats','json,svg,html,pptx','--approve','--timeout','60'])
                if code!=0 or not data.get('ok'): failures.append({'id':cid,'code':code,'error':data.get('error')})
                else: artifacts+=len(data.get('artifacts',[]))
            assert not failures, failures[:5]
            return {'families':len(reps),'artifacts':artifacts}
        record('family_full_format_representatives',family_full)
        def clean_root():
            with tempfile.TemporaryDirectory(dir=Path.cwd(),prefix='.upd-clean-') as tmp:
                tmp=Path(tmp); shutil.copytree(TOOL_DIR,tmp/TOOL_NAME)
                (tmp/'SKILL.md').write_text('# Test skill root\n')
                r=subprocess.run([sys.executable,f'{TOOL_NAME}/tools-presentation-design.py','build','--input',f'{TOOL_NAME}/asset-example-deck.json','--output','out','--formats','json,pptx'],cwd=tmp,capture_output=True,text=True,timeout=120)
                assert r.returncode==0, r.stdout+r.stderr
                assert (tmp/'out'/'deck.pptx').exists()
                r2=subprocess.run([sys.executable,f'{TOOL_NAME}/tools-presentation-design.py','capabilities','--category','1','--format','text'],cwd=tmp,capture_output=True,text=True,timeout=60)
                assert r2.returncode==0 and 'audience-and-outcome-framing' in r2.stdout
                return {'clean_root':True,'skill_md_count':1}
        record('clean_temporary_skill_root',clean_root)
        def structure():
            files=[p.name for p in TOOL_DIR.iterdir() if p.is_file()]
            assert 'SKILL.md' not in files
            assert not any(p.is_dir() for p in TOOL_DIR.iterdir())
            assert all(f.startswith(('tools-','knowledge-','asset-')) for f in files)
            assert files.count('knowledge-readme.md')==1
            return {'files':len(files)}
        record('package_structure',structure)
    finally:
        shutil.rmtree(root,ignore_errors=True)
    ok=all(r['ok'] for r in results)
    return {'ok':ok,'quick':quick,'tests':results,'summary':{'passed':sum(r['ok'] for r in results),'failed':sum(not r['ok'] for r in results),'total':len(results)}}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--quick',action='store_true'); p.add_argument('--output')
    a=p.parse_args(); report=run_tests(a.quick)
    text=json.dumps(report,indent=2)+'\n'
    if a.output: Path(a.output).write_text(text)
    print(text)
    raise SystemExit(0 if report['ok'] else 1)
