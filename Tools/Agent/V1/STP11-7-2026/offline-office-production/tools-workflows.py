"""Catalog normalization, mail merge, comparison, records and offline adapters."""
import difflib
import hashlib
import html
from html.parser import HTMLParser
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import zipfile

class PlainHTML(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('style','script'):self.skip+=1
        if tag in ('p','div','li','br','h1','h2','h3','h4','tr'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('style','script'):self.skip=max(0,self.skip-1)
        if tag in ('p','div','li','h1','h2','h3','h4','tr'):self.parts.append('\n')
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)


def catalog_parse(d,c):
    source=c.path(c.require(d,'source'),True)
    if source.suffix.lower()=='.pdf':
        from pypdf import PdfReader
        text='\n'.join(p.extract_text() or '' for p in PdfReader(source).pages)
    elif source.suffix.lower()=='.docx':
        from docx import Document
        doc=Document(source);text='\n'.join(p.text for p in doc.paragraphs)+'\n'+'\n'.join('\n'.join(x.text for x in row.cells) for t in doc.tables for row in t.rows)
    else:
        text=source.read_text(encoding='utf-8')
        if re.search(r'<[a-zA-Z][^>]*>',text):
            p=PlainHTML();p.feed(text);text=''.join(p.parts)
    text=re.sub(r'FULL OFFLINE DOCUMENT AND OFFICE PRODUCTION\s*\|\s*SKILL CATALOG\s*\n\d+\n','',text)
    lines=[]
    for line in text.splitlines():
        line=re.sub(r'^\s*(?:#{1,6}\s*|[-*•]\s*|\d+[.)]\s*)','',line)
        line=line.replace('**','').replace('`','').strip()
        if line:lines.append(re.sub(r'\s+',' ',line))
    builtin=json.loads(Path(__file__).with_name('asset-capability-catalog.json').read_text())
    # Category-only requests resolve actual entries in the supplied catalog.
    if len(lines)==1:
        label=lines[0].removeprefix('Category:').strip().casefold()
        matched=[r for r in builtin if r['category'].casefold()==label]
        if matched:
            if 'dest' in d:c.json_write(d['dest'],matched)
            return {'capabilities':matched,'count':len(matched)}
    # Support unlabeled names followed by Description, including numbered lists.
    normalized=[]
    for i,line in enumerate(lines):
        if i+1<len(lines) and re.match(r'Description\s*:',lines[i+1],re.I) and not re.match(r'(?:Skill Tool Name|Skill Name|Name)\s*:',line,re.I):
            line='Skill Name: '+line
        normalized.append(line)
    lines=normalized
    rows=[];category=d.get('category','offline-office-production');name=None;desc=[]
    def flush():
        if name and desc:rows.append({'category':category,'name':name,'description':' '.join(desc)})
    for line in lines:
        m=re.match(r'(?:Skill Tool Name|Skill Name|Name)\s*:\s*(.+)',line,re.I)
        if m:flush();name=m[1];desc=[]
        elif re.match(r'Description\s*:',line,re.I):desc.append(re.sub(r'^Description\s*:\s*','',line,flags=re.I))
        elif name and desc:
            if line in [x['category'] for x in builtin] or line.startswith('Category:'):
                flush();category=line.removeprefix('Category:').strip();name=None;desc=[]
            elif line.startswith('Recommended Catalog-to-Skill'):break
            else:desc.append(line)
        elif not name:category=line.removeprefix('Category:').strip()
    flush()
    if not rows:
        if len(lines)<2:c.fail('invalid_input','Supply a name and description, or labeled capability entries.')
        rows=[{'category':d.get('category','offline-office-production'),'name':lines[0],'description':' '.join(lines[1:])}]
    for i,row in enumerate(rows):row['id']=f'parsed-{i+1:03d}'
    if 'dest' in d:c.json_write(d['dest'],rows)
    return {'capabilities':rows,'count':len(rows)}


def compare(d,c,native):
    a=native.extract({'source':c.require(d,'left')},c)['text'].splitlines()
    b=native.extract({'source':c.require(d,'right')},c)['text'].splitlines()
    if len(a)+len(b)>20000:c.fail('limit','Comparison exceeds 20000 text lines.')
    diff='\n'.join(difflib.unified_diff(a,b,fromfile=d['left'],tofile=d['right'],lineterm=''))
    if 'dest' in d:c.write(d['dest'],lambda p:p.write_text(diff,encoding='utf-8'))
    return {'equal':a==b,'diff':diff,'scope':'Extracted body text; not formatting, tracked-change or visual comparison.'}


def mail_merge(d,c):
    from docx import Document
    template=c.path(c.require(d,'template'),True);c.zip_check(template)
    records=c.bounded(c.require(d,'records',list));prefix=c.require(d,'dest_prefix');outputs=[]
    if not records:c.fail('invalid_input','Provide at least one record.')
    # Preflight all tokens and targets before committing any record.
    targets=[f'{prefix}-{i+1:04d}.docx' for i in range(len(records))]
    for target in targets:
        if c.path(target).exists():c.need_approval('Mail merge would replace an existing output.')
    base=Document(template)
    def paragraphs(doc):
        yield from doc.paragraphs
        for t in doc.tables:
            for row in t.rows:
                for cell in row.cells:yield from cell.paragraphs
        for s in doc.sections:
            yield from s.header.paragraphs;yield from s.footer.paragraphs
    tokens=set(re.findall(r'\{\{([a-zA-Z0-9.-]+)\}\}','\n'.join(p.text for p in paragraphs(base))))
    if not tokens:c.fail('invalid_input','No {{field}} tokens in supported template paragraphs.')
    for record in records:
        if not isinstance(record,dict) or tokens-set(record):c.fail('invalid_input','Each record must supply all template tokens: '+', '.join(sorted(tokens)))
    for target,record in zip(targets,records):
        c.check();doc=Document(template)
        for paragraph in paragraphs(doc):
            if '{{' in paragraph.text:
                text=re.sub(r'\{\{([a-zA-Z0-9.-]+)\}\}',lambda m:str(record[m[1]]),paragraph.text)
                # Preserve paragraph style and first run format; complex mixed runs are deliberately simplified.
                if paragraph.runs:
                    paragraph.runs[0].text=text
                    for run in paragraph.runs[1:]:run.text=''
                else:paragraph.add_run(text)
        c.write(target,lambda p:doc.save(p));outputs.append(target)
    return {'artifacts':outputs,'warning':'Replacement paragraphs retain first-run formatting only. Text boxes, nested tables and fields are not merged.'}


def baseline(d,c):
    packages={}
    for name in ('python-docx','openpyxl','python-pptx','pypdf','reportlab','PyMuPDF','cryptography'):
        try:packages[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:packages[name]=None
    executables={x:bool(shutil.which(x)) for x in ('libreoffice','tesseract','verapdf','qpdf','lp','openssl','fc-list')}
    return {'python':platform.python_version(),'system':platform.system(),'packages':packages,'executables':executables,'network_used':False,'note':'Presence is not installation readiness or compatibility certification.'}


def adapter(d,c):
    kind=c.require(d,'adapter');src=c.path(c.require(d,'source'),True);dest=c.require(d,'dest')
    timeout=d.get('timeout',60)
    if not isinstance(timeout,int) or not 1<=timeout<=120:c.fail('invalid_input','Adapter timeout must be 1–120 seconds.')
    if kind not in ('libreoffice-pdf','tesseract-text'):c.fail('unsupported','Only libreoffice-pdf and tesseract-text adapters are implemented.')
    exe=shutil.which('libreoffice' if kind=='libreoffice-pdf' else 'tesseract')
    if not exe:c.fail('integration_unavailable','Install the approved local '+('LibreOffice' if kind=='libreoffice-pdf' else 'Tesseract')+' runtime and its dependencies. No download is attempted.')
    c.need_approval('Launching a local document engine processes untrusted active content. Run inside an OS sandbox; this tool is not a sandbox.')
    with tempfile.TemporaryDirectory(prefix='office-adapter-') as td:
        work=Path(td)
        if kind=='libreoffice-pdf':
            if src.suffix.lower() not in ('.docx','.pptx','.xlsx','.odt','.ods','.odp'):c.fail('format','Unsupported office source.')
            if src.suffix.lower() in ('.docx','.pptx','.xlsx'):c.zip_check(src)
            argv=[exe,'-env:UserInstallation='+ (work/'profile').as_uri(),'--headless','--convert-to','pdf','--outdir',td,str(src)]
            output=work/(src.stem+'.pdf')
            if not dest.endswith('.pdf'):c.fail('format','Destination must be .pdf.')
        else:
            if src.suffix.lower() not in ('.png','.jpg','.jpeg','.tif','.tiff'):c.fail('format','OCR accepts raster image files only.')
            from PIL import Image
            with Image.open(src) as im:
                if im.width*im.height>25000000:c.fail('limit','OCR image exceeds 25 megapixels.')
            lang=d.get('language','eng')
            if not re.fullmatch(r'[a-zA-Z0-9]+(?:\+[a-zA-Z0-9]+)*',lang):c.fail('invalid_input','Invalid OCR language identifier.')
            argv=[exe,str(src),str(work/'ocr'),'-l',lang];output=work/'ocr.txt'
        with (work/'log').open('wb') as log:
            proc=subprocess.run(argv,stdout=log,stderr=log,timeout=timeout,stdin=subprocess.DEVNULL,cwd=td)
        if proc.returncode or not output.is_file():c.fail('adapter_failed','Local engine failed; verify format, runtime, fonts or language models. No output committed.')
        c.write(dest,lambda p:shutil.copyfile(output,p))
    return {'artifact':dest,'adapter':kind,'warning':'Adapter is bounded by timeout, not by an OS security sandbox. Validate generated output separately.'}


def records(d,c):
    action=c.require(d,'action')
    if action=='backup':
        files=c.bounded(c.require(d,'sources',list));dest=c.require(d,'dest')
        if not files:c.fail('invalid_input','Backup requires files.')
        seen=set();members=[]
        for value in files:
            p=c.path(value,True)
            if p.suffix.lower() in ('.zip','.tar','.gz','.7z'):c.fail('unsafe_archive','Nested archive inputs are prohibited.')
            if value in seen:c.fail('invalid_input','Duplicate backup member.')
            seen.add(value);members.append((value,p))
        if sum(p.stat().st_size for _,p in members)>c.MAX_BYTES:c.fail('limit','Backup input exceeds 64 MiB.')
        def save(p):
            with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:
                for name,source in members:c.check();z.write(source,name)
        c.write(dest,save)
        return {'artifact':dest,'members':files}
    if action=='verify':
        p=c.path(c.require(d,'source'),True);c.zip_check(p)
        with zipfile.ZipFile(p) as z:return {'valid_crc':True,'members':z.namelist(),'warning':'CRC is not authenticity or cryptographic trust.'}
    if action=='restore':
        c.need_approval('Restoring a backup writes multiple files.')
        p=c.path(c.require(d,'source'),True);c.zip_check(p);prefix=c.require(d,'dest_prefix')
        with zipfile.ZipFile(p) as z:
            members=[i for i in z.infolist() if not i.is_dir()]
            if len(members)>1000:c.fail('limit','Restore exceeds 1000 files.')
            for i in members:
                target=prefix+'/'+i.filename;c.path(target)
                if c.path(target).exists() and not c.APPROVED:c.fail('conflict','Target exists.')
            outputs=[]
            for i in members:
                c.check();target=prefix+'/'+i.filename;c.write(target,lambda q,i=i:q.write_bytes(z.read(i)));outputs.append(target)
        return {'artifacts':outputs}
    if action=='digest':
        p=c.path(c.require(d,'source'),True)
        return {'source':d['source'],'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
    c.fail('unsupported','Unknown records action.')
