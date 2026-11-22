from pathlib import Path
import hashlib, html, json, mimetypes, os, re, sys
ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs'
SECTIONS=('Tools','Knowledge','Skills')
IGNORE_NAMES={'.git','.github','docs','Scripts','__pycache__','.DS_Store','Thumbs.db'}
TEXT_EXT={'.txt','.md','.json','.jsonl','.csv','.tsv','.xml','.yaml','.yml','.toml','.ini','.cfg','.conf','.py','.ps1','.cmd','.bat','.js','.mjs','.cjs','.ts','.tsx','.jsx','.html','.css','.scss','.sql','.sh','.srt','.vtt','.log','.rst','.tex','.svg'}
BINARY_AGENT_EXT={'.onnx','.bin','.safetensors','.pt','.pth','.gguf','.tflite','.wasm','.jar','.exe','.dll','.pyd','.zip','.7z','.rar','.tar','.gz','.pdf','.docx','.xlsx','.pptx','.mp3','.wav','.flac','.mp4','.mov','.mkv','.webm','.png','.jpg','.jpeg','.webp','.gif','.svg','.glb','.gltf','.obj','.fbx','.blend'}

def h(x): return html.escape(str(x),quote=True)
def norm(p): return p.as_posix()
def size_text(n):
    units=['B','KiB','MiB','GiB','TiB']; x=float(n)
    for u in units:
        if x<1024 or u==units[-1]: return f'{x:.0f} {u}' if u=='B' else f'{x:.1f} {u}'
        x/=1024

def title_from(path): return re.sub(r'[_-]+',' ',path.stem).strip() or path.name

def description(path):
    if path.suffix.lower() not in TEXT_EXT or path.stat().st_size>2*1024*1024: return ''
    try:
        text=path.read_text('utf-8-sig',errors='replace')[:12000]
    except Exception: return ''
    for raw in text.splitlines():
        line=re.sub(r'^[#>*\-\s]+','',raw).strip()
        if len(line)>=12 and not line.startswith(('{','[','<','//','/*')): return line[:280]
    return ''

def sha256(path):
    d=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): d.update(chunk)
    return d.hexdigest()

entries=[]; categories=[]
for section in SECTIONS:
    base=ROOT/section
    for folder in sorted((p for p in base.rglob('*') if p.is_dir()),key=lambda p:norm(p.relative_to(ROOT)).lower()):
        rel=folder.relative_to(ROOT)
        if any(part.startswith('.') for part in rel.parts): continue
        categories.append({'section':section,'name':folder.name,'path':norm(rel),'depth':len(rel.parts)-1})
    for path in sorted((p for p in base.rglob('*') if p.is_file()),key=lambda p:norm(p.relative_to(ROOT)).lower()):
        rel=path.relative_to(ROOT)
        if path.name=='.gitkeep' or any(part.startswith('.') for part in rel.parts): continue
        ext=path.suffix.lower(); kind='readable' if ext in TEXT_EXT else ('binary' if ext in BINARY_AGENT_EXT else 'other')
        entries.append({'section':section,'category':rel.parts[1] if len(rel.parts)>1 else 'Uncategorized','subcategories':list(rel.parts[1:-1]),'name':path.name,'title':title_from(path),'path':norm(rel),'url':'../'+norm(rel),'bytes':path.stat().st_size,'size':size_text(path.stat().st_size),'extension':ext or '(none)','mime':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','kind':kind,'searchable':kind=='readable','downloadable':True,'description':description(path),'sha256':sha256(path)})
summary={s:{'files':sum(1 for x in entries if x['section']==s),'bytes':sum(x['bytes'] for x in entries if x['section']==s),'categories':len({x['category'] for x in entries if x['section']==s})} for s in SECTIONS}
catalog={'schemaVersion':'1.0','name':"Emmre's Agent Tools",'sections':list(SECTIONS),'summary':summary,'categories':categories,'files':entries}
DOCS.mkdir(exist_ok=True); (DOCS/'catalog.json').write_text(json.dumps(catalog,indent=2,ensure_ascii=False),encoding='utf-8')

css='''
:root{color-scheme:light dark;--bg:#07111f;--panel:#102038;--line:#294766;--text:#eff7ff;--muted:#adbed0;--accent:#78c7ff;--ok:#5ee0a0}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,Segoe UI,sans-serif}header,main,footer{max-width:1200px;margin:auto;padding:24px}nav{display:flex;flex-wrap:wrap;gap:14px;margin-top:12px}a{color:var(--accent)}.hero{padding:34px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:15px}.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}.muted{color:var(--muted)}.tag{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 8px;margin:2px;font-size:.82rem}.button{display:inline-block;border:1px solid var(--accent);border-radius:9px;padding:8px 12px;text-decoration:none;margin:3px}input,select{padding:12px;border:1px solid var(--line);border-radius:9px;font:inherit;background:var(--panel);color:var(--text)}.controls{display:grid;grid-template-columns:1fr auto auto;gap:10px;margin:18px 0}.file{overflow-wrap:anywhere}.file[hidden]{display:none}.count{color:var(--muted)}code{overflow-wrap:anywhere}.skip{position:absolute;left:-9999px}.skip:focus{left:10px;top:10px;background:white;color:black;padding:8px}@media(max-width:700px){.controls{grid-template-columns:1fr}header,main,footer{padding:18px}}
'''
(DOCS/'assets').mkdir(exist_ok=True); (DOCS/'assets/site.css').write_text(css.strip(),encoding='utf-8')

def shell(title,body):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{h(title)}"><title>{h(title)}</title><link rel="stylesheet" href="assets/site.css"><link rel="alternate" type="application/json" href="catalog.json"></head><body><a class="skip" href="#main">Skip to content</a><header><strong>Emmre's Agent Tools</strong><nav><a href="index.html">Home</a><a href="tools.html">Tools</a><a href="knowledge.html">Knowledge</a><a href="skills.html">Skills</a><a href="downloads.html">Downloads</a><a href="catalog.json">JSON catalog</a></nav></header><main id="main">{body}</main><footer><p>Generated from the repository's Tools, Knowledge, and Skills folders.</p></footer></body></html>'''

def section_cards(section):
    groups={}
    for e in entries:
        if e['section']==section: groups.setdefault(e['category'],[]).append(e)
    cats=sorted({p.name for p in (ROOT/section).iterdir() if p.is_dir() and not p.name.startswith('.')},key=str.lower)
    cards=''
    for cat in cats:
        files=groups.get(cat,[]); cards+=f'<article class="card"><h2>{h(cat)}</h2><p>{len(files)} file(s)</p><a href="downloads.html?section={h(section)}&category={h(cat)}">Browse downloads</a></article>'
    return cards or '<article class="card"><p>No categories yet.</p></article>'

home=f'''<section class="hero"><h1>Emmre's Agent Tools</h1><p>A self-updating GitHub Pages library for tools, knowledge, and reusable skills. Add files or folders to the three source areas, rebuild the index, and push.</p></section><section class="grid"><article class="card"><h2>Tools</h2><p>{summary['Tools']['files']} files in {summary['Tools']['categories']} populated categories.</p><a href="tools.html">Browse tool categories</a></article><article class="card"><h2>Knowledge</h2><p>{summary['Knowledge']['files']} files in {summary['Knowledge']['categories']} populated categories.</p><a href="knowledge.html">Browse knowledge categories</a></article><article class="card"><h2>Skills</h2><p>{summary['Skills']['files']} files in {summary['Skills']['categories']} populated categories.</p><a href="skills.html">Browse skill categories</a></article><article class="card"><h2>All downloads</h2><p>{len(entries)} indexed files totaling {size_text(sum(x['bytes'] for x in entries))}.</p><a href="downloads.html">Search and download everything</a></article></section><section class="panel"><h2>How it updates</h2><ol><li>Add folders and files under <code>Tools/</code>, <code>Knowledge/</code>, or <code>Skills/</code>.</li><li>Run <code>1. Update Index.cmd</code>.</li><li>Run <code>2. Push to GitHub LFS.cmd</code>.</li></ol></section>'''
(DOCS/'index.html').write_text(shell("Emmre's Agent Tools",home),encoding='utf-8')
for sec in SECTIONS:
    intro={'Tools':'Executables, models, scripts, templates, media utilities, and other working assets organized by category.','Knowledge':'References, guides, datasets, standards, examples, and supporting material organized by category.','Skills':'Reusable skill folders, instructions, resources, scripts, schemas, and assets organized by category.'}[sec]
    (DOCS/f'{sec.lower()}.html').write_text(shell(sec,f'<h1>{sec}</h1><p>{intro}</p><section class="grid">{section_cards(sec)}</section>'),encoding='utf-8')

file_cards=''
for i,e in enumerate(entries):
    trail=' / '.join(e['subcategories']) or 'Uncategorized'; desc=f'<p>{h(e["description"])}</p>' if e['description'] else ''
    file_cards+=f'''<article class="card file" data-section="{h(e['section'])}" data-category="{h(e['category'])}" data-search="{h((e['title']+' '+e['path']+' '+e['description']).lower())}"><h2>{h(e['title'])}</h2>{desc}<p class="muted">{h(e['section'])} / {h(trail)} · {h(e['size'])} · {h(e['extension'])}</p><span class="tag">{h(e['kind'])}</span><span class="tag">{'searchable' if e['searchable'] else 'downloadable asset'}</span><p><a class="button" href="{h(e['url'])}">Open</a><a class="button" href="{h(e['url'])}" download>Download</a></p></article>'''
options=''.join(f'<option value="{h(s)}">{h(s)}</option>' for s in SECTIONS)
downloads=f'''<h1>Downloads</h1><p>Search, open, or download every indexed file. Folder structure becomes the category structure automatically.</p><div class="controls"><input id="q" type="search" placeholder="Search names, paths, and descriptions"><select id="section"><option value="">All sections</option>{options}</select><select id="category"><option value="">All categories</option></select></div><p class="count" id="count">Showing {len(entries)} files.</p><section class="grid" id="files">{file_cards or '<article class="card"><p>No files indexed yet. Add files to Tools, Knowledge, or Skills, then update the index.</p></article>'}</section><script>const files=[...document.querySelectorAll('.file')],q=document.querySelector('#q'),s=document.querySelector('#section'),c=document.querySelector('#category'),count=document.querySelector('#count');const params=new URLSearchParams(location.search);s.value=params.get('section')||'';function categories(){{const vals=[...new Set(files.filter(x=>!s.value||x.dataset.section===s.value).map(x=>x.dataset.category))].sort();c.innerHTML='<option value="">All categories</option>'+vals.map(v=>`<option>${{v}}</option>`).join('');const requested=params.get('category');if(requested&&vals.includes(requested))c.value=requested}}function apply(){{const text=q.value.toLowerCase().trim();let n=0;files.forEach(x=>{{const ok=(!text||x.dataset.search.includes(text))&&(!s.value||x.dataset.section===s.value)&&(!c.value||x.dataset.category===c.value);x.hidden=!ok;if(ok)n++}});count.textContent=`Showing ${{n}} of ${{files.length}} files.`}}s.addEventListener('change',()=>{{categories();apply()}});q.addEventListener('input',apply);c.addEventListener('change',apply);categories();apply();</script>'''
(DOCS/'downloads.html').write_text(shell('Downloads',downloads),encoding='utf-8')
(DOCS/'.nojekyll').write_text('',encoding='ascii')
print(f'Indexed {len(entries)} files across {len(categories)} folders.')
