"""Bounded, offline processors. No subprocesses, network, or code execution."""
import ast
from collections import Counter
import csv
import hashlib
import html
import io
import json
import math
import re
import statistics
import zipfile
from pathlib import Path
from html.parser import HTMLParser

LIMIT = 8 * 1024 * 1024
OPS = ['file-inspect', 'text-review', 'document-html', 'csv-analyze', 'json-validate', 'python-audit', 'html-audit', 'contrast', 'evidence-search', 'zip-build', 'catalog-ingest']


def path(root, name, exists=True):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Expected a nonempty forward-slash relative path')
    p = Path(name)
    if p.is_absolute() or '..' in p.parts or ':' in name:
        raise ValueError('Absolute, drive and traversal paths are forbidden')
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Path escapes workspace')
    if exists and not resolved.is_file():
        raise ValueError('Missing or unreadable file: ' + name)
    return resolved


def read(root, name):
    p = path(root, name)
    if p.stat().st_size > LIMIT:
        raise ValueError('Input exceeds 8 MiB: ' + name)
    return p.read_bytes()


def fingerprint(root, name):
    b = read(root, name)
    return {'path': name, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(); self.text = []; self.nodes = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'br'): self.text.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip - 1)
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3'): self.text.append('\n')
    def handle_data(self, data):
        if not self.skip: self.text.append(data)


def ingest(text):
    text = re.sub(r'<h([1-6])\b[^>]*>(.*?)</h\1>', lambda m: '\n' + ('### ' if int(m[1]) >= 3 else '## ') + re.sub('<[^>]*>', '', m[2]) + '\n', text, flags=re.I | re.S)
    parser = Markup(); parser.feed(text)
    clean = ''.join(parser.text) if re.search(r'<[a-zA-Z][^>]*>', text) else text
    records = []; category = None; current = None
    for raw in clean.splitlines():
        line = re.sub(r'^\s*(?:\d+[.)]\s*|[-*•]\s*)', '', raw).strip()
        if not line: continue
        if raw.lstrip().startswith('#'):
            title = line.lstrip('#').strip()
            if raw.lstrip().startswith('###'):
                current = {'category': category, 'name': title, 'description': ''}; records.append(current)
            else: category = title; current = None
        elif re.match(r'^\s*\d+[.)]\s+', raw):
            current = {'category': category, 'name': line, 'description': ''}; records.append(current)
        elif ':' in line and len(line.split(':', 1)[0]) < 100:
            name, desc = line.split(':', 1)
            current = {'category': category, 'name': name.strip(), 'description': desc.strip()}; records.append(current)
        elif current is not None:
            current['description'] = (current['description'] + ' ' + line).strip()
        else:
            current = {'category': category, 'name': line, 'description': ''}; records.append(current)
    return {'records': records, 'normalized_text': clean, 'warning': 'Ambiguous unmarked category/name boundaries require review. Markdown ## category / ### name or Name: description is unambiguous.'}


def execute(op, params, root):
    if op not in OPS: raise ValueError('Unknown local operation: ' + op)
    if not isinstance(params, dict): raise ValueError('params must be an object')
    if op == 'contrast':
        def lum(s):
            if not re.fullmatch(r'#[0-9a-fA-F]{6}', s): raise ValueError('Colors must be #RRGGBB')
            v = [int(s[i:i+2], 16)/255 for i in (1, 3, 5)]
            v = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in v]
            return sum(x*y for x,y in zip(v, [.2126,.7152,.0722]))
        a,b = lum(params['foreground']),lum(params['background']); ratio=(max(a,b)+.05)/(min(a,b)+.05)
        return {'ratio': ratio, 'aa-normal': ratio >= 4.5, 'aa-large': ratio >= 3, 'aaa-normal': ratio >= 7}
    if op == 'zip-build':
        names = params['files']
        if not isinstance(names,list) or not 1 <= len(names) <= 400 or len(set(names)) != len(names): raise ValueError('files must contain 1..400 distinct paths')
        blobs = [(n,read(root,n)) for n in names]
        if sum(len(b) for _,b in blobs) > 32*1024*1024: raise ValueError('Archive input exceeds 32 MiB')
        if any(n.lower().endswith(('.zip','.tar','.gz','.7z')) for n,_ in blobs): raise ValueError('Nested archives forbidden')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            for n,b in blobs: z.writestr(n,b)
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
            if z.testzip() is not None: raise ValueError('ZIP verification failed')
        return {'files':names,'bytes':len(buf.getvalue())},buf.getvalue()
    if op == 'file-inspect':
        names=params['files']
        if not isinstance(names,list) or not 1 <= len(names) <= 400: raise ValueError('files must contain 1..400 paths')
        results=[]
        for n in names:
            f=fingerprint(root,n); b=read(root,n); suffix=Path(n).suffix.lower()
            if suffix == '.json': json.loads(b)
            if suffix in ('.zip','.docx','.xlsx','.pptx'):
                with zipfile.ZipFile(io.BytesIO(b)) as z:
                    if sum(i.file_size for i in z.infolist()) > 32*1024*1024: raise ValueError('Expanded ZIP exceeds 32 MiB')
                    if len(z.infolist()) > 500: raise ValueError('Too many ZIP entries')
                    if z.testzip(): raise ValueError('Corrupt ZIP')
                    f['entries'] = z.namelist()
            f['validation']='container/readability only; not layout or semantic verification';results.append(f)
        return {'files':results}
    text=read(root,params['input']).decode('utf-8-sig')
    if op == 'catalog-ingest': return ingest(text)
    if op == 'text-review':
        counts=Counter(text.splitlines())
        words=re.findall(r'\b\w+\b',text); sentences=[s for s in re.split(r'[.!?]+',text) if s.strip()]
        return {'words':len(words),'sentences':len(sentences),'average-words-per-sentence':len(words)/max(1,len(sentences)), 'long-sentences':[s.strip() for s in sentences if len(s.split())>25], 'duplicate-lines':sorted(l for l,n in counts.items() if l.strip() and n>1), 'limitations':'Mechanical review only; no meaning-preserving rewrite or fact checking.'}
    if op == 'document-html':
        title=params.get('title','Document')
        body='\n'.join('<p>'+html.escape(p).replace('\n','<br>')+'</p>' for p in text.split('\n\n') if p.strip())
        doc='<!doctype html><html lang="en"><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>body{max-width:72ch;margin:3rem auto;padding:0 1rem;font:18px/1.6 system-ui;color:#17212b}p{white-space:normal}@media print{body{margin:1cm}}</style><h1>'+html.escape(title)+'</h1>'+body+'</html>'
        return {'format':'html','title':title}, doc.encode()
    if op == 'csv-analyze':
        reader=csv.DictReader(io.StringIO(text)); rows=list(reader)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames): raise ValueError('CSV requires unique headers')
        if len(rows)>100000: raise ValueError('CSV exceeds 100000 rows')
        if any(None in r or any(v is None for v in r.values()) for r in rows): raise ValueError('Ragged CSV row')
        columns={}
        for name in reader.fieldnames:
            vals=[r[name] for r in rows if r[name].strip()]; nums=[]
            for v in vals:
                try: n=float(v)
                except ValueError: break
                if not math.isfinite(n): raise ValueError('Nonfinite numeric data')
                nums.append(n)
            info={'missing':len(rows)-len(vals),'distinct':len(set(vals))}
            if vals and len(nums)==len(vals): info.update(min=min(nums),max=max(nums),sum=sum(nums),mean=statistics.mean(nums),median=statistics.median(nums))
            columns[name]=info
        return {'rows':len(rows),'columns':columns,'duplicate-rows':len(rows)-len({tuple(r.values()) for r in rows})}
    if op == 'json-validate':
        obj=json.loads(text);required=params.get('required',[])
        if not isinstance(required,list) or not all(isinstance(k,str) for k in required): raise ValueError('required must be a string list')
        missing=[k for k in required if not isinstance(obj,dict) or k not in obj]
        return {'valid':not missing,'missing':missing,'type':type(obj).__name__,'scope':'JSON syntax and top-level required keys; not full JSON Schema'}
    if op == 'python-audit':
        tree=ast.parse(text);findings=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.Call):
                name=ast.unparse(n.func)
                if name in ('eval','exec','os.system','subprocess.run','subprocess.Popen'):findings.append({'line':n.lineno,'call':name,'severity':'review'})
        return {'syntax-valid':True,'findings':findings,'scope':'Static heuristic only; code is never executed'}
    if op == 'html-audit':
        parser=Markup();parser.feed(text);nodes=parser.nodes;findings=[]
        for tag,a in nodes:
            if tag=='img' and 'alt' not in a: findings.append('Image missing alt')
            if tag=='html' and not a.get('lang'):findings.append('HTML missing lang')
            if tag=='input' and a.get('type') not in ('hidden','submit','button') and not any(k in a for k in ('aria-label','aria-labelledby','id')):findings.append('Input lacks label association candidate')
        return {'findings':findings,'scope':'Static hints only; no browser, keyboard or screen-reader certification'}
    if op == 'evidence-search':
        q=params['query']
        if not isinstance(q,str) or not q.strip() or len(q)>1000:raise ValueError('query must be 1..1000 characters')
        terms=q.casefold().split();hits=[]
        for i,line in enumerate(text.splitlines(),1):
            score=sum(t in line.casefold() for t in terms)
            if score:hits.append({'line':i,'score':score,'excerpt':line[:1000]})
        return {'matches':sorted(hits,key=lambda x:(-x['score'],x['line']))[:100], 'source':fingerprint(root,params['input']), 'scope':'Local lexical retrieval, not web research or truth verification'}
    raise ValueError('Operation not implemented')
