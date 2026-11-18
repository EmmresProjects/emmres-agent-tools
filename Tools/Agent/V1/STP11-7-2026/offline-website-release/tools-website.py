#!/usr/bin/env python3
"""Bounded, offline static-site toolkit. No subprocesses or network operations."""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
MAX_INPUT = 2 * 1024 * 1024
MAX_FILES = 1000
MAX_ARTIFACT = 100 * 1024 * 1024
GATES = ('offline_restore', 'build_tests', 'browser_journeys', 'accessibility',
         'performance', 'security_privacy', 'seo', 'backup_restore',
         'rollback', 'dns_tls_hosting', 'monitoring', 'release_approval')

class Problem(Exception): pass

class Parser(argparse.ArgumentParser):
    def error(self, message): raise Problem(message)

def read_bytes(path, maximum=MAX_INPUT):
    p = Path(path)
    if p.is_symlink() or not p.is_file(): raise Problem('Input must be an existing regular non-symlink file: ' + str(p))
    if p.stat().st_size > maximum: raise Problem('Input exceeds byte limit: ' + str(p))
    with p.open('rb') as f:
        data = f.read(maximum + 1)
    if len(data) > maximum: raise Problem('Input exceeds byte limit: ' + str(p))
    return data

def read_json(path):
    try: return json.loads(read_bytes(path).decode('utf-8'))
    except (ValueError, UnicodeError) as e: raise Problem('Invalid UTF-8 JSON in ' + str(path) + ': ' + str(e))

def write_json(data, out, replace=False):
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if not out:
        sys.stdout.write(text); return
    p = Path(out)
    if not p.parent.is_dir(): raise Problem('Output parent directory does not exist')
    if p.is_symlink() or (p.exists() and not p.is_file()): raise Problem('Output must not be a symlink or directory')
    if p.exists() and not replace: raise Problem('Output exists; choose a new path or approve replacement with --replace')
    fd, tmp = tempfile.mkstemp(prefix='.website-output-', dir=str(p.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(text)
        if replace: os.replace(tmp, p)
        else:
            # hard link fails if another writer created the target
            os.link(tmp, p); os.unlink(tmp)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags=[]; self.title=''; self.in_title=False; self.ids=set(); self.labels=set(); self.controls=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); self.tags.append((tag,a))
        if a.get('id'): self.ids.add(a['id'])
        if tag=='label' and a.get('for'): self.labels.add(a['for'])
        if tag in ('input','select','textarea') and a.get('type','').lower() not in ('hidden','submit','button','reset','image'):
            self.controls.append(a)
        if tag=='title': self.in_title=True
    def handle_endtag(self, tag):
        if tag=='title': self.in_title=False
    def handle_data(self,data):
        if self.in_title: self.title+=data

def bounded_files(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir(): raise Problem('Site root must be an existing non-symlink directory')
    found=[]
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(base)/d).is_symlink())
        for name in sorted(files):
            p=Path(base)/name
            if p.is_symlink(): continue
            found.append(p)
            if len(found)>MAX_FILES: raise Problem('Site exceeds 1000 regular files')
    return root, found

def audit(root):
    root, files=bounded_files(root); pages=[p for p in files if p.suffix.lower() in ('.html','.htm')]
    if not pages: raise Problem('No HTML files found under site root')
    findings=[]; external=set(); root_real=root.resolve()
    def add(path,code,severity,message): findings.append({'path':path,'code':code,'severity':severity,'message':message})
    for p in pages:
        rel=p.relative_to(root).as_posix()
        try: text=read_bytes(p).decode('utf-8-sig')
        except UnicodeError: raise Problem('HTML must be UTF-8: '+rel)
        page=Page(); page.feed(text); tags=page.tags
        if not re.search(r'<!doctype\s+html\s*>',text,re.I): add(rel,'doctype','warning','Add an HTML5 doctype.')
        if not any(t=='html' and a.get('lang','').strip() for t,a in tags): add(rel,'html-lang','error','Add a language to the html element.')
        if not page.title.strip(): add(rel,'title','error','Add a non-empty page title.')
        if not any(t=='meta' and a.get('name','').lower()=='viewport' and a.get('content','').strip() for t,a in tags): add(rel,'viewport','warning','Add a viewport meta tag.')
        if not any(t=='meta' and a.get('name','').lower()=='description' and a.get('content','').strip() for t,a in tags): add(rel,'description','warning','Add a meta description.')
        if not any(t=='h1' for t,a in tags): add(rel,'h1','warning','Add a primary heading.')
        if not any(t=='main' for t,a in tags): add(rel,'main','warning','Consider a main landmark.')
        for t,a in tags:
            if t=='img' and 'alt' not in a: add(rel,'image-alt','error','Image missing alt attribute: '+a.get('src','(no src)'))
            if t=='a' and a.get('target')=='_blank' and 'noopener' not in a.get('rel','').lower().split(): add(rel,'blank-noopener','warning','Add rel=noopener for target=_blank.')
            for key in (('href',) if t=='a' else ('src',) if t in ('img','script','source','video','audio') else ('href',) if t=='link' else ()):
                value=a.get(key,'').strip()
                if not value or value.startswith(('#','data:','mailto:','tel:')): continue
                u=urlsplit(value)
                if u.scheme or u.netloc:
                    if t!='a': external.add(value)
                    if u.scheme.lower()=='javascript': add(rel,'javascript-url','error','Avoid javascript: URLs.')
                    continue
                decoded=unquote(u.path)
                if not decoded: continue
                target=(root / decoded.lstrip('/')) if decoded.startswith('/') else (p.parent / decoded)
                target=target.resolve()
                if not target.is_relative_to(root_real): add(rel,'path-escape','error','Local reference escapes site root: '+value); continue
                if target.is_dir(): target=target/'index.html'
                if not target.exists(): add(rel,'broken-local-reference','error','Local target not found: '+value)
        for a in page.controls:
            if not (a.get('aria-label','').strip() or a.get('aria-labelledby','').strip() or (a.get('id') in page.labels)):
                add(rel,'form-label-review','warning','Check accessible label for form control '+a.get('name','(unnamed)')+'; wrapping labels may not be detected.')
    for u in sorted(external): findings.append({'path':'','code':'external-resource','severity':'warning','message':'External resource requires network or separate caching: '+u})
    return {'operation':'audit','pages':len(pages),'files':len(files),'findings':findings,'summary':{'errors':sum(f['severity']=='error' for f in findings),'warnings':sum(f['severity']=='warning' for f in findings)},'limitations':['Static heuristics only; not a browser, WCAG, security, SEO, performance, or production certification.','Dynamic routes, CSS imports, JavaScript behavior and fragment validity are not evaluated.']}

def safe_rel(value):
    if not isinstance(value,str) or not value or '\\' in value or '\x00' in value: raise Problem('Artifact path must be a nonempty forward-slash relative path')
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in ('.','..') for x in value.split('/')) or ':' in value: raise Problem('Unsafe artifact path: '+value)
    return p

def verify(root, manifest):
    root=Path(root)
    if root.is_symlink() or not root.is_dir(): raise Problem('Artifact root must be a non-symlink directory')
    manifest=read_json(manifest)
    if not isinstance(manifest,dict) or not isinstance(manifest.get('artifacts'),list) or not 1<=len(manifest['artifacts'])<=MAX_FILES: raise Problem('Manifest must contain 1–1000 artifacts')
    entries=[]; seen=set()
    for item in manifest['artifacts']:
        if not isinstance(item,dict): raise Problem('Each artifact must be an object')
        p=safe_rel(item.get('path')); expected=item.get('sha256')
        if str(p) in seen: raise Problem('Duplicate artifact: '+str(p))
        seen.add(str(p))
        if not isinstance(expected,str) or not re.fullmatch(r'[a-fA-F0-9]{64}',expected): raise Problem('Each artifact requires a 64-character SHA-256: '+str(p))
        entries.append((p,expected.lower()))
    result=[]
    for p, expected in entries:
        cur=root
        for part in p.parts:
            cur=cur/part
            if cur.is_symlink(): raise Problem('Symlink in artifact path: '+str(p))
        if not cur.is_file(): result.append({'path':str(p),'status':'missing'}); continue
        if cur.stat().st_size>MAX_ARTIFACT: raise Problem('Artifact exceeds 100 MiB: '+str(p))
        digest=hashlib.sha256(); size=0
        with cur.open('rb') as f:
            while True:
                b=f.read(1024*1024)
                if not b: break
                size+=len(b)
                if size>MAX_ARTIFACT: raise Problem('Artifact exceeds 100 MiB: '+str(p))
                digest.update(b)
        actual=digest.hexdigest(); result.append({'path':str(p),'status':'verified' if actual==expected else 'mismatch','actual_sha256':actual,'bytes':size})
    return {'operation':'verify-artifacts','artifacts':result,'all_verified':all(x['status']=='verified' for x in result),'limitations':['Hash matching is not proof of origin, licensing, compatibility, safety, or complete offline dependency closure.']}

def readiness(path):
    data=read_json(path)
    if not isinstance(data,dict) or not isinstance(data.get('gates'),dict): raise Problem('Readiness input must contain a gates object')
    unknown=set(data['gates'])-set(GATES)
    if unknown: raise Problem('Unknown gates: '+', '.join(sorted(unknown)))
    results=[]
    for gate in GATES:
        v=data['gates'].get(gate, {'status':'unknown'})
        if not isinstance(v,dict) or v.get('status') not in ('pass','fail','unknown','not_applicable'): raise Problem('Invalid gate status for '+gate)
        evidence=v.get('evidence',''); owner=v.get('owner','')
        if not isinstance(evidence,str) or not isinstance(owner,str): raise Problem('Evidence and owner must be strings: '+gate)
        if v['status'] in ('pass','not_applicable') and not evidence.strip(): raise Problem('Passing or not-applicable gate needs evidence or justification: '+gate)
        results.append({'gate':gate,'status':v['status'],'owner':owner,'evidence':evidence})
    ready=all(x['status'] in ('pass','not_applicable') for x in results) and results[-1]['status']=='pass'
    return {'operation':'readiness','ready':ready,'gates':results,'limitations':['Statuses are user assertions, not independently verified. Release approval must pass, not be marked not applicable. No deployment is performed.']}

def scaffold(input_path, output_dir):
    data=read_json(input_path)
    if not isinstance(data,dict): raise Problem('Site input must be an object')
    for key in ('title','description','heading','body'):
        if not isinstance(data.get(key),str) or not data[key].strip() or len(data[key])>10000: raise Problem('Required nonempty string (maximum 10000 characters): '+key)
    lang=data.get('language','en')
    if not isinstance(lang,str) or not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*',lang): raise Problem('Invalid language tag')
    target=Path(output_dir)
    if target.resolve().is_relative_to(HERE): raise Problem('Generated site must be outside the Skill Tool folder')
    if target.exists() or target.is_symlink(): raise Problem('Output directory already exists; select a new directory')
    if not target.parent.is_dir(): raise Problem('Output parent directory must exist')
    esc=lambda x: html.escape(x,quote=True)
    document='''<!doctype html>\n<html lang="%s">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="description" content="%s">\n<title>%s</title>\n<link rel="stylesheet" href="styles.css">\n</head>\n<body>\n<a class="skip" href="#main">Skip to content</a>\n<header><p>%s</p></header>\n<main id="main"><h1>%s</h1><p>%s</p></main>\n</body>\n</html>\n'''%(esc(lang),esc(data['description']),esc(data['title']),esc(data['title']),esc(data['heading']),esc(data['body']))
    css='''* { box-sizing: border-box; }\nbody { margin: 0; color: #152332; background: #f8fafc; font: 1rem/1.6 system-ui, sans-serif; }\nheader, main { max-width: 70rem; margin: auto; padding: 1.5rem; }\nheader { border-bottom: 1px solid #bcc8d4; font-weight: 700; }\nh1 { line-height: 1.15; }\n.skip { position: absolute; top: -5rem; }\n.skip:focus { top: 0; background: white; color: #111; padding: .5rem; z-index: 1; }\n'''
    tmp=Path(tempfile.mkdtemp(prefix='.website-build-',dir=str(target.parent)))
    try:
        (tmp/'index.html').write_text(document,encoding='utf-8'); (tmp/'styles.css').write_text(css,encoding='utf-8')
        check=audit(tmp)
        if check['summary']['errors']: raise Problem('Generated site did not pass its local static checks')
        # mkdir reservation prevents overwriting an existing destination; populate after reservation.
        target.mkdir()
        try:
            for f in tmp.iterdir(): shutil.move(str(f),str(target/f.name))
        except BaseException:
            shutil.rmtree(target); raise
    finally: shutil.rmtree(tmp,ignore_errors=True)
    return {'operation':'scaffold','created':str(target),'files':['index.html','styles.css'],'audit':check['summary'],'limitations':['A minimal static starting point, not a framework, backend, complete website, accessibility certification, or deployment.']}

def catalog(query, category):
    data=read_json(HERE/'asset-catalog.json'); out=[]
    for cat in data['categories']:
        if category and str(cat['number'])!=category and category.lower() not in cat['name'].lower(): continue
        for item in cat['items']:
            if query and query.lower() not in (item['name']+' '+item['description']).lower(): continue
            out.append({'category_number':cat['number'],'category':cat['name'],**item})
    return {'operation':'catalog','count':len(out),'items':out,'note':'Source concepts are a searchable reference, not claims of implementation.'}

def main():
    ap=Parser(description='Offline static-site and release evidence utilities. Does not deploy or access the network.')
    sub=ap.add_subparsers(dest='cmd',required=True,parser_class=Parser)
    caps=sub.add_parser('capabilities',help='List implemented operations and scope')
    cat=sub.add_parser('catalog',help='Search source catalog; entries are not implemented capabilities')
    cat.add_argument('--query',default='');cat.add_argument('--category',default='')
    au=sub.add_parser('audit',help='Static HTML and local-reference heuristics');au.add_argument('--site',required=True)
    ve=sub.add_parser('verify-artifacts',help='Check local artifacts against SHA-256 manifest');ve.add_argument('--root',required=True);ve.add_argument('--manifest',required=True)
    re_=sub.add_parser('readiness',help='Summarize self-reported release gate evidence');re_.add_argument('--input',required=True)
    sc=sub.add_parser('scaffold',help='Generate a small local static site');sc.add_argument('--input',required=True);sc.add_argument('--site',required=True)
    for sp in (caps,cat,au,ve,re_,sc):
        sp.add_argument('--output',help='JSON report path; defaults to stdout');sp.add_argument('--replace',action='store_true',help='Approve replacing an existing report')
    args=ap.parse_args()
    # Prevent report output from overwriting any declared input or packaged resources.
    if args.output:
        output=Path(args.output).resolve()
        protected=[Path(__file__).resolve(),(HERE/'asset-catalog.json').resolve()]
        for x in ('input','manifest'):
            if getattr(args,x,None): protected.append(Path(getattr(args,x)).resolve())
        for x in ('site','root'):
            if getattr(args,x,None) and output.is_relative_to(Path(getattr(args,x)).resolve()): raise Problem('Report output must be outside the input or generated site/artifact directory')
        if output in protected or output.is_relative_to(HERE): raise Problem('Report cannot overwrite an input or reside in the Skill Tool folder')
        if Path(args.output).is_symlink() or (Path(args.output).exists() and not Path(args.output).is_file()): raise Problem('Output must not be a symlink or directory')
        if Path(args.output).exists() and not args.replace: raise Problem('Output exists; choose a new path or approve replacement with --replace')
        if not Path(args.output).parent.is_dir(): raise Problem('Output parent directory does not exist')
    if args.cmd=='capabilities':
        result={'operation':'capabilities','implemented':{'catalog':'Search the 30-category, 1238-concept source reference (not implementations).','scaffold':'Generate an escaped, minimal HTML/CSS site.','audit':'Run bounded, static HTML and local-reference checks.','verify-artifacts':'Check local files against supplied SHA-256 values.','readiness':'Summarize self-reported release gates.'},'not_implemented':'Most catalog concepts, including frameworks, backend, payments, browser testing, full accessibility and deployment; no external integrations.'}
    elif args.cmd=='catalog': result=catalog(args.query,args.category)
    elif args.cmd=='scaffold': result=scaffold(args.input,args.site)
    elif args.cmd=='audit': result=audit(args.site)
    elif args.cmd=='verify-artifacts': result=verify(args.root,args.manifest)
    else: result=readiness(args.input)
    write_json(result,args.output,args.replace)
    if args.cmd=='audit' and result['summary']['errors']: return 1
    if args.cmd=='verify-artifacts' and not result['all_verified']: return 1
    if args.cmd=='readiness' and not result['ready']: return 1
    return 0

if __name__=='__main__':
    try: sys.exit(main())
    except (Problem, OSError, ValueError) as e:
        print(json.dumps({'error':{'type':type(e).__name__,'message':str(e)}},ensure_ascii=False),file=sys.stderr); sys.exit(2)
    except KeyboardInterrupt:
        print(json.dumps({'error':{'type':'cancelled','message':'Operation cancelled'}}),file=sys.stderr); sys.exit(130)
