from pathlib import Path
import json,html,re,hashlib,mimetypes
R=Path(__file__).resolve().parents[1]; D=R/'docs'; sections=('Tools','Knowledge','Skills')
text={'.txt','.md','.json','.jsonl','.csv','.tsv','.xml','.yaml','.yml','.toml','.ini','.cfg','.conf','.py','.ps1','.cmd','.bat','.js','.mjs','.cjs','.ts','.tsx','.jsx','.html','.css','.sql','.sh','.srt','.vtt','.svg'}
def h(x):return html.escape(str(x),quote=True)
def sz(n):
 x=float(n)
 for u in ['B','KiB','MiB','GiB']:
  if x<1024 or u=='GiB':return f'{x:.0f} {u}' if u=='B' else f'{x:.1f} {u}'
  x/=1024
def desc(p):
 if p.suffix.lower() not in text or p.stat().st_size>2097152:return ''
 try:t=p.read_text('utf-8-sig',errors='replace')[:10000]
 except:return ''
 for q in t.splitlines():
  q=re.sub(r'^[#>*\-\s]+','',q).strip()
  if len(q)>10:return q[:240]
 return ''
files=[];cats=[]
for s in sections:
 for d in sorted((x for x in (R/s).rglob('*') if x.is_dir()),key=lambda x:x.as_posix().lower()):
  rel=d.relative_to(R)
  if not any(x.startswith('.') for x in rel.parts):cats.append({'section':s,'path':rel.as_posix(),'name':d.name})
 for p in sorted((x for x in (R/s).rglob('*') if x.is_file()),key=lambda x:x.as_posix().lower()):
  rel=p.relative_to(R)
  if p.name=='.gitkeep' or any(x.startswith('.') for x in rel.parts):continue
  ext=p.suffix.lower(); d=desc(p); digest=hashlib.sha256(p.read_bytes()).hexdigest()
  files.append({'section':s,'category':rel.parts[1] if len(rel.parts)>1 else 'Uncategorized','subcategories':list(rel.parts[1:-1]),'name':p.name,'title':re.sub(r'[_-]+',' ',p.stem),'path':rel.as_posix(),'url':'../'+rel.as_posix(),'bytes':p.stat().st_size,'size':sz(p.stat().st_size),'extension':ext or '(none)','mime':mimetypes.guess_type(p.name)[0] or 'application/octet-stream','kind':'readable' if ext in text else 'binary','searchable':ext in text,'downloadable':True,'description':d,'sha256':digest})
summary={s:{'files':sum(x['section']==s for x in files),'categories':len({x['category'] for x in files if x['section']==s})} for s in sections}
(D/'catalog.json').write_text(json.dumps({'schemaVersion':'1.1','name':"Emmre's Agent Tools",'summary':summary,'categories':cats,'files':files},indent=2,ensure_ascii=False),encoding='utf-8')
(D/'assets/site.css').write_text(':root{color-scheme:light dark;--b:#07111f;--p:#102038;--l:#294766;--t:#eff7ff;--a:#78c7ff}*{box-sizing:border-box}body{margin:0;background:var(--b);color:var(--t);font:16px/1.55 system-ui,Segoe UI,sans-serif}header,main,footer{max-width:1200px;margin:auto;padding:24px}nav{display:flex;gap:14px;flex-wrap:wrap}a{color:var(--a)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:15px}.card,.panel{background:var(--p);border:1px solid var(--l);border-radius:14px;padding:17px}.controls{display:grid;grid-template-columns:1fr auto auto;gap:10px}input,select,button{padding:11px;font:inherit}button{border:1px solid var(--a);border-radius:7px;background:transparent;color:var(--a);cursor:pointer}button:disabled{opacity:.6;cursor:wait}.file[hidden]{display:none}@media(max-width:700px){.controls{grid-template-columns:1fr}}',encoding='utf-8')
def shell(title,body):return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title><link rel="stylesheet" href="assets/site.css"></head><body><header><h1>Emmre\'s Agent Tools</h1><nav><a href="index.html">Home</a><a href="tools.html">Tools</a><a href="knowledge.html">Knowledge</a><a href="skills.html">Skills</a><a href="downloads.html">Downloads</a><a href="catalog.json">JSON</a></nav></header><main>{body}</main><footer>Generated from repository folders.</footer></body></html>'
def cards(s):
 names=sorted({x.name for x in (R/s).iterdir() if x.is_dir()},key=str.lower);return ''.join(f'<article class="card"><h2>{h(n)}</h2><p>{sum(1 for x in files if x["section"]==s and x["category"]==n)} files</p><a href="downloads.html?section={s}&category={h(n)}">Browse</a></article>' for n in names)
home=''.join(f'<article class="card"><h2>{s}</h2><p>{summary[s]["files"]} files</p><a href="{s.lower()}.html">Browse categories</a></article>' for s in sections)
(D/'index.html').write_text(shell('Home',f'<p>Add any folders and files under Tools, Knowledge, or Skills. The index adapts automatically.</p><section class="grid">{home}</section>'),encoding='utf-8')
for s in sections:(D/f'{s.lower()}.html').write_text(shell(s,f'<h1>{s}</h1><section class="grid">{cards(s)}</section>'),encoding='utf-8')
fcs=''.join(f'<article class="card file" data-section="{h(x["section"])}" data-category="{h(x["category"])}" data-search="{h((x["path"]+" "+x["description"]).lower())}"><h2>{h(x["title"])}</h2><p>{h(x["description"])}</p><p>{h(x["path"])} · {x["size"]} · {x["kind"]}</p><a href="{h(x["url"])}" target="_blank" rel="noopener">Open</a> · <button class="download-button" type="button" data-url="{h(x["url"])}" data-name="{h(x["name"])}">Download</button></article>' for x in files)
opts=''.join(f'<option>{s}</option>' for s in sections)
js="""<script>const a=[...document.querySelectorAll('.file')],q=document.querySelector('#q'),s=document.querySelector('#s'),c=document.querySelector('#c'),n=document.querySelector('#n'),p=new URLSearchParams(location.search);s.value=p.get('section')||'';function cats(){let v=[...new Set(a.filter(x=>!s.value||x.dataset.section==s.value).map(x=>x.dataset.category))].sort();c.innerHTML='<option value="">All categories</option>'+v.map(x=>`<option>${x}</option>`).join('');if(v.includes(p.get('category')))c.value=p.get('category')}function go(){let k=q.value.toLowerCase(),z=0;a.forEach(x=>{let y=(!k||x.dataset.search.includes(k))&&(!s.value||x.dataset.section==s.value)&&(!c.value||x.dataset.category==c.value);x.hidden=!y;if(y)z++});n.textContent=`Showing ${z} of ${a.length} files.`}async function forceDownload(button){const original=button.textContent;button.disabled=true;button.textContent='Preparing...';try{const response=await fetch(button.dataset.url,{cache:'no-store'});if(!response.ok)throw new Error(`HTTP ${response.status}`);const blob=await response.blob();const objectUrl=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=objectUrl;anchor.download=button.dataset.name||'download';anchor.style.display='none';document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(objectUrl),30000)}catch(error){alert(`Download failed: ${error.message}. Use Open, then save the file from your browser.`)}finally{button.disabled=false;button.textContent=original}}document.addEventListener('click',event=>{const button=event.target.closest('.download-button');if(button)forceDownload(button)});q.oninput=go;s.onchange=()=>{cats();go()};c.onchange=go;cats();go()</script>"""
(D/'downloads.html').write_text(shell('Downloads',f'<h1>Downloads</h1><div class="controls"><input id="q" placeholder="Search"><select id="s"><option value="">All sections</option>{opts}</select><select id="c"></select></div><p id="n"></p><section class="grid">{fcs or "<article class=\"card\">No files yet.</article>"}</section>{js}'),encoding='utf-8')
(D/'.nojekyll').write_text('');print(f'Indexed {len(files)} files and {len(cats)} folders.')
