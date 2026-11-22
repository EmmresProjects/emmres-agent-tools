"""Capability dispatch and family engines for the unified presentation design tool.

Every catalog capability is reachable through run --capability <id>. External platforms
receive portable local artifacts plus explicit handoffs; no live API calls are made.
"""
from __future__ import annotations
import copy, csv, io, json, math, re
from datetime import datetime, timedelta
from pathlib import Path
import tools_core as core
import tools_render as render
import tools_raster as raster
import tools_pptx as pptx

ALLOWED_FORMATS={"json","svg","html","pptx","png","pdf","gif"}

def load_registry(config):
    ref=config.get('paths',{}).get('capabilities',f'{core.TOOL_NAME}/asset-capabilities.json')
    reg=core.load_json(core.resolve_resource(ref),8*1024*1024)
    caps={c['id']:c for c in reg['capabilities']}
    if len(caps)!=reg['capability_count']:
        raise core.ToolError('REGISTRY_MISMATCH','Capability registry count does not match entries.')
    return reg,caps

def list_capabilities(reg,*,category=None,query=None,family=None):
    items=reg['capabilities']
    if category is not None: items=[c for c in items if c['category']==int(category)]
    if family: items=[c for c in items if c['family']==family]
    if query:
        q=query.lower(); items=[c for c in items if q in c['name'].lower() or q in c['id'] or q in c['description'].lower() or q in c['category_name'].lower()]
    return items

def _rel(path):
    try: return str(Path(path).resolve().relative_to(Path.cwd().resolve()))
    except Exception: return str(path)

def _write_json(path,data,config):
    core.atomic_write(Path(path),json.dumps(data,indent=2,ensure_ascii=False)+'\n',overwrite=config.get('_overwrite',False),approved=config.get('_approved',False),retries=config.get('retry',{}).get('attempts',2),backoff_ms=config.get('retry',{}).get('backoff_ms',100))
    return _rel(path)

def _write_text(path,text,config):
    core.atomic_write(Path(path),text,overwrite=config.get('_overwrite',False),approved=config.get('_approved',False),retries=config.get('retry',{}).get('attempts',2),backoff_ms=config.get('retry',{}).get('backoff_ms',100))
    return _rel(path)

def write_deck_artifacts(deck,outdir,config,themes,formats,*,variant='standard',prefix='deck',motion=False,handout=False,kiosk=False,native=True,deadline=None):
    deadline=deadline or core.Deadline()
    warnings=core.validate_deck(deck,config)
    theme=core.resolve_theme(deck,themes)
    deck=core.apply_theme(deck,theme)
    layouts=[]; artifacts=[]; outdir=Path(outdir)
    for i,s in enumerate(deck['slides']):
        deadline.check(f'layout slide {i+1}')
        layouts.append(render.layout_slide(deck,s,i,theme,variant))
    if 'json' in formats:
        artifacts.append(_write_json(outdir/f'{prefix}.json',deck,config))
    if 'svg' in formats:
        (outdir/'slides').mkdir(parents=True,exist_ok=True)
        for i,(s,l) in enumerate(zip(deck['slides'],layouts)):
            deadline.check(f'svg slide {i+1}')
            artifacts.append(_write_text(outdir/'slides'/f'slide-{i+1:03d}.svg',render.render_svg(deck,s,l,theme,i,motion=motion),config))
    if 'html' in formats:
        artifacts.append(_write_text(outdir/f'{prefix}.html',render.render_html(deck,theme,layouts,handout=handout,motion=motion),config))
    images=[]
    if 'png' in formats or 'pdf' in formats or 'gif' in formats:
        (outdir/'slides').mkdir(parents=True,exist_ok=True)
        for i,l in enumerate(layouts):
            deadline.check(f'raster slide {i+1}')
            img=raster.render_png(l,background=theme['colors']['background']); images.append(img)
            if 'png' in formats:
                p=outdir/'slides'/f'slide-{i+1:03d}.png'; img.save(p,format='PNG'); artifacts.append(_rel(p))
        if 'png' in formats and images:
            p=outdir/'contact-sheet.png'; raster.write_contact_sheet(images,p); artifacts.append(_rel(p))
    if 'pdf' in formats and images:
        p=outdir/f'{prefix}.pdf'; raster.write_pdf(images,p); artifacts.append(_rel(p))
    if 'gif' in formats and images:
        durations=[min(10000,max(200,int(s.get('duration_seconds',3)*1000))) for s in deck['slides']]
        p=outdir/f'{prefix}-motion.gif'; raster.write_gif(images,p,durations_ms=durations); artifacts.append(_rel(p))
    if 'pptx' in formats:
        deadline.check('pptx')
        cfg=dict(config); info=pptx.build_pptx(deck,themes,outdir/f'{prefix}.pptx',cfg,variant=variant,native=native,kiosk=kiosk)
        artifacts.append(_rel(outdir/f'{prefix}.pptx'))
    return {'artifacts':artifacts,'warnings':warnings,'slide_count':len(deck['slides']),'theme':theme.get('name')}

def _base_result(ctx,data,artifacts=None,warnings=None):
    return {'ok':True,'capability':ctx['entry']['id'],'family':ctx['entry']['family'],'preset':ctx['entry']['preset'],'mode':ctx['entry']['mode'],'data':data,'artifacts':artifacts or [],'warnings':warnings or [],'metrics':{'elapsed_seconds':ctx['deadline'].elapsed}}

def _emit(ctx,deck,**kw):
    formats=kw.pop('formats',ctx['formats'])
    res=write_deck_artifacts(deck,ctx['outdir'],ctx['config'],ctx['themes'],formats,variant=kw.pop('variant',ctx['variant']),deadline=ctx['deadline'],**kw)
    return res

def _default_deck(ctx):
    return ctx['deck']

def _ensure_slide(deck,layout,key=None):
    for s in deck['slides']:
        if s.get('layout')==layout and (key is None or s.get(key)):
            return s
    s={'id':f'{layout}-1','layout':layout,'title':layout.title(),'duration_seconds':45}
    if key: s[key]={}
    deck['slides'].append(s); return s

def _md(title,sections):
    lines=[f'# {title}','']
    for h,body in sections:
        lines.append(f'## {h}')
        if isinstance(body,list): lines.extend(f'- {x}' for x in body)
        elif isinstance(body,dict): lines.append('```json\n'+json.dumps(body,indent=2,ensure_ascii=False)+'\n```')
        else: lines.append(str(body))
        lines.append('')
    return '\n'.join(lines)

FRAMEWORKS={
 'executive-narrative-architecture':['Decision','Context','Evidence','Options','Recommendation','Ask'],
 'problem-solution-storytelling':['Problem','Impact','Solution','Proof','Next step'],
 'situation-complication-resolution-structuring':['Situation','Complication','Resolution','Evidence','Decision'],
 'investor-pitch-architecture':['Problem','Solution','Market','Traction','Business model','Team','Ask'],
 'sales-deck-narrative-design':['Buyer context','Pain','Value','Proof','Offer','Next step'],
 'educational-lesson-sequencing':['Objective','Prior knowledge','Concept','Example','Practice','Check','Summary'],
 'product-demo-storyboarding':['Context','Workflow','Demo','Proof','Limits','Call to action'],
 'conference-talk-structuring':['Hook','Thesis','Story','Evidence','Implication','Close'],
 'workshop-flow-design':['Welcome','Goal','Input','Activity','Debrief','Decision','Next step'],
 'portfolio-story-curation':['Theme','Selected work','Process','Impact','Reflection','Contact'],
 'case-study-narrative-builder':['Client','Challenge','Approach','Result','Lesson','Next step'],
}

def family_strategy(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; params=ctx['params']
    framework=FRAMEWORKS.get(preset,['Audience','Outcome','Message','Evidence','Decision','Next step'])
    audience=params.get('audience') or deck.get('audience') or 'General audience'
    outcome=params.get('outcome') or deck.get('purpose') or 'Reach a clear decision'
    brief={'audience':audience,'outcome':outcome,'framework':framework,'through_line':params.get('through_line') or deck.get('title'),'evidence':[s.get('title') for s in deck['slides'] if s.get('layout') in {'chart','table','kpi'}],'call_to_action':params.get('call_to_action') or outcome,'gaps':[]}
    if not any(s.get('layout') in {'chart','table','kpi'} for s in deck['slides']): brief['gaps'].append('Add evidence slides before external use.')
    deck['audience']=audience; deck['purpose']=outcome
    # Add agenda and closing if missing
    if not any(s.get('layout')=='section' for s in deck['slides']):
        deck['slides'].insert(1,{'id':'agenda','layout':'bullets','title':'Agenda','bullets':framework,'duration_seconds':20})
    if not any(s.get('layout')=='closing' for s in deck['slides']):
        deck['slides'].append({'id':'decision','layout':'closing','title':outcome,'subtitle':'Decision requested','duration_seconds':30})
    arts=[_write_json(ctx['outdir']/'brief.json',brief,ctx['config']),_write_text(ctx['outdir']/'brief.md',_md('Presentation brief',[('Audience',audience),('Outcome',outcome),('Framework',framework),('Evidence gaps',brief['gaps'])]),ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,brief,arts+res['artifacts'],res['warnings'])

def family_planning(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; params=ctx['params']
    inventory=[{'id':s['id'],'layout':s['layout'],'title':s.get('title'),'words':len(core.slide_text(s).split()),'duration_seconds':s.get('duration_seconds',45),'purpose':s['layout']} for s in deck['slides']]
    total_words=sum(i['words'] for i in inventory); total_seconds=sum(i['duration_seconds'] for i in inventory)
    plan={'inventory':inventory,'slide_count':len(inventory),'word_count':total_words,'estimated_minutes':round(total_seconds/60,2),'density_flags':[i['id'] for i in inventory if i['words']>ctx['config']['accessibility']['max_words_per_slide']],'localization_ready':all(not re.search(r'[^\x00-\x7F]',core.slide_text(s)) or s.get('translations') for s in deck['slides'])}
    if preset in {'speaker-notes-planner','content-density-balancer'}:
        for s in deck['slides']:
            if not s.get('notes'): s['notes']=f"Purpose: {s['layout']}. Key message: {s.get('title','')}. Transition: connect this point to the next decision."
    if preset=='agenda-and-navigation-builder' and not any(s.get('title','').lower()=='agenda' for s in deck['slides']):
        deck['slides'].insert(1,{'id':'agenda','layout':'bullets','title':'Agenda','bullets':[s.get('title','') for s in deck['slides'][:8]],'duration_seconds':20})
    if preset=='appendix-architecture':
        deck['slides'].append({'id':'appendix','layout':'section','title':'Appendix','duration_seconds':10})
    if preset=='one-idea-per-slide-decomposer':
        new=[]
        for s in deck['slides']:
            bullets=s.get('bullets') or []
            if len(bullets)>ctx['config']['accessibility']['max_bullets']:
                for i in range(0,len(bullets),ctx['config']['accessibility']['max_bullets']):
                    ns=copy.deepcopy(s); ns['id']=f"{s['id']}-{i//ctx['config']['accessibility']['max_bullets']+1}"; ns['bullets']=bullets[i:i+ctx['config']['accessibility']['max_bullets']]; new.append(ns)
            else: new.append(s)
        deck['slides']=new
    if preset=='deck-length-and-timing-estimator':
        plan['recommendation']='Trim to one idea per slide and rehearse with a timer.' if total_seconds>params.get('target_minutes',20)*60 else 'Timing is within the target.'
    arts=[_write_json(ctx['outdir']/'plan.json',plan,ctx['config']),_write_text(ctx['outdir']/'plan.md',_md('Slide plan',[('Inventory',inventory),('Timing',f"{plan['estimated_minutes']} minutes"),('Density flags',plan['density_flags'])]),ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,plan,arts+res['artifacts'],res['warnings'])

def family_pptx(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; params=ctx['params']
    variant=ctx['variant']; kiosk=preset=='powerpoint-kiosk-presentation-builder' or params.get('kiosk',False)
    if preset=='powerpoint-theme-builder': deck['theme']=params.get('theme','neutral')
    if preset=='powerpoint-notes-and-handout-designer':
        for s in deck['slides']:
            s.setdefault('notes',f"Discuss {s.get('title','this slide')} and connect it to the audience outcome.")
    if preset=='powerpoint-section-manager' and not deck.get('sections'):
        deck['sections']=[{'id':'main','title':'Main'}]
        for s in deck['slides']: s['section']='main'
    if preset=='powerpoint-template-packager':
        deck={'schema_version':'1.0','title':'Reusable template','theme':params.get('theme','neutral'),'canvas':ctx['config']['defaults']['canvas'],'sections':[],'slides':[{'id':l,'layout':l,'title':l.replace('-',' ').title(),'bullets':['Replace this placeholder'],'duration_seconds':30} for l in ['title','bullets','chart','table','diagram','timeline','closing']],'metadata':{'template':True}}
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'],checks=['accessibility','brand','links','charts','privacy','motion','geometry','media'])
    res=_emit(ctx,deck,variant=variant,kiosk=kiosk,handout=preset=='powerpoint-notes-and-handout-designer')
    report={'preset':preset,'kiosk':kiosk,'compatibility_audit':audit,'notes':'PPTX is editable; native app rendering was not available in this environment.'}
    arts=[_write_json(ctx['outdir']/'pptx-report.json',report,ctx['config'])]+res['artifacts']
    return _base_result(ctx,report,arts,res['warnings'])

def family_handoff(ctx):
    deck=copy.deepcopy(ctx['deck']); entry=ctx['entry']; params=ctx['params']
    platform=entry['category_name'] if entry['category'] in {5,6,7,8} else entry['name']
    handoff={'platform':platform,'capability':entry['name'],'mode':'portable-handoff','live_api_called':False,'approval_required':entry['approval_required'],'approved':ctx['approve'],'inputs':{'deck_title':deck.get('title'),'slide_count':len(deck['slides'])},'mapping':{'editable_deck':'PPTX','vector_preview':'SVG','interactive_preview':'HTML','data':'JSON/CSV'},'steps':['Review the portable PPTX/SVG/HTML artifacts.','Import or recreate the deck in the target platform using its native editor.','Map theme tokens, fonts, and layouts to platform-native equivalents.','Re-run accessibility, link, and fidelity checks inside the target platform.'],'unsupported':['Live API execution','Platform-specific permissions/publishing','Native animation/morph fidelity','Vendor-only templates or assets'],'security':['No credentials are embedded.','Publishing, permission changes, and macro/script execution require explicit human approval outside this tool.']}
    if entry['approval_required'] and not ctx['approve']:
        raise core.ToolError('APPROVAL_REQUIRED','This handoff is security-sensitive and requires --approve.',{'capability':entry['id']},core.EXIT_APPROVAL)
    arts=[_write_json(ctx['outdir']/'handoff.json',handoff,ctx['config']),_write_text(ctx['outdir']/'handoff.md',_md(f'{platform} handoff',[('Capability',entry['name']),('Steps',handoff['steps']),('Unsupported',handoff['unsupported']),('Security',handoff['security'])]),ctx['config'])]
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,handoff,arts+res['artifacts'],res['warnings'])

def family_geometry(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; canvas=deck.setdefault('canvas',copy.deepcopy(ctx['config']['defaults']['canvas']))
    report={'preset':preset,'canvas':copy.deepcopy(canvas),'changes':[]}
    if preset=='grid-system-architect': canvas['grid']=int(p.get('grid',12)); report['changes'].append('grid')
    if preset in {'margin-and-safe-area-designer','guide-and-ruler-manager'}:
        canvas['margin']=float(p.get('margin',canvas.get('margin',64))); canvas['safe']=float(p.get('safe',canvas.get('safe',48))); report['changes'].append('safe-area')
    if preset=='responsive-canvas-layout-planner':
        report['variants']=[{'name':'16:9','width':1280,'height':720},{'name':'4:3','width':1024,'height':768},{'name':'square','width':1080,'height':1080},{'name':'story','width':1080,'height':1920}]
    dx=float(p.get('dx',0)); dy=float(p.get('dy',0)); scale=float(p.get('scale',1))
    for s in deck['slides']:
        for obj in s.get('objects') or []:
            if preset in {'coordinate-based-object-positioner','multi-object-transformation-controller','anchor-and-constraint-designer'}:
                obj['x']=obj.get('x',0)*scale+dx; obj['y']=obj.get('y',0)*scale+dy; obj['w']=obj.get('w',100)*scale; obj['h']=obj.get('h',80)*scale
        if preset=='object-distribution-specialist' and s.get('objects'):
            objs=s['objects']; n=len(objs)
            for i,obj in enumerate(objs): obj['x']=canvas['margin']+(canvas['width']-2*canvas['margin'])*(i+0.5)/n-obj.get('w',100)/2
        if preset=='layer-and-z-order-manager' and s.get('objects'):
            for i,obj in enumerate(sorted(s['objects'],key=lambda o:o.get('z',0))): obj['z']=i
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'],checks=['geometry'])
    report['geometry_audit']=audit
    arts=[_write_json(ctx['outdir']/'geometry-report.json',report,ctx['config'])]
    res=_emit(ctx,deck,variant='modular' if preset=='modular-grid-layout-designer' else ctx['variant'])
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

LAYOUT_VARIANTS={'rule-of-thirds-composer':'thirds','golden-ratio-layout-designer':'golden','modular-grid-layout-designer':'modular','asymmetrical-balance-designer':'asymmetric','symmetrical-balance-designer':'symmetric','whitespace-strategist':'minimal','focal-point-designer':'focal','minimalist-layout-designer':'minimal','dense-information-layout-designer':'dense','magazine':'magazine','poster':'poster'}
def family_layout(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; variant=LAYOUT_VARIANTS.get(preset,ctx['variant'])
    deck.setdefault('metadata',{})['layout_variant']=variant
    if preset in {'visual-hierarchy-designer','contrast-led-composition-designer'}:
        for s in deck['slides']:
            if s.get('bullets') and len(s['bullets'])>6: s['bullets']=s['bullets'][:6]
    report={'preset':preset,'variant':variant,'principles':['hierarchy','alignment','spacing','contrast','reading path'],'slide_count':len(deck['slides'])}
    arts=[_write_json(ctx['outdir']/'layout-report.json',report,ctx['config'])]
    res=_emit(ctx,deck,variant=variant)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_typography(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; theme=core.resolve_theme(deck,ctx['themes']); report={'preset':preset,'changes':[]}
    if preset=='typeface-pairing-curator':
        theme['fonts']={'heading':p.get('heading','Georgia'),'body':p.get('body','Arial'),'mono':p.get('mono','Consolas')}; report['changes'].append('font-pairing')
    if preset in {'font-size-scale-architect','heading-hierarchy-designer','presentation-typography-system-designer'}:
        factor=float(p.get('scale',1.1)); theme['type_scale']={k:v*factor for k,v in theme['type_scale'].items()}; report['changes'].append('type-scale')
    if preset=='accessible-typography-reviewer':
        theme['type_scale']['body']=max(theme['type_scale']['body'],24); theme['type_scale']['caption']=max(theme['type_scale']['caption'],18); report['changes'].append('minimum-sizes')
    if preset in {'right-to-left-layout-specialist','multilingual-typography-designer'}:
        deck['direction']=p.get('direction','rtl' if preset=='right-to-left-layout-specialist' else deck.get('direction','ltr')); report['changes'].append('direction')
    if preset=='cjk-presentation-typography-specialist':
        theme['fonts']={'heading':p.get('heading','Noto Sans CJK'),'body':p.get('body','Noto Sans CJK'),'mono':'Noto Sans Mono CJK'}; report['changes'].append('cjk-fonts')
    if preset=='font-embedding-and-substitution-specialist':
        report['substitutions']={'Arial':['Liberation Sans','Helvetica'],'Georgia':['Liberation Serif','Times New Roman'],'Consolas':['Liberation Mono','Courier New']}; report['embedding']='PowerPoint font embedding is application-controlled; verify on the presenting device.'
    deck['theme']=theme; deck['resolved_theme']=theme
    fit=[]
    for s in deck['slides']:
        text=core.slide_text(s); fit.append({'id':s['id'],'chars':len(text),'estimated_lines':math.ceil(len(text)/80),'flag':len(text)>600})
    report['fit_audit']=fit
    arts=[_write_json(ctx['outdir']/'typography-report.json',report,ctx['config']),_write_json(ctx['outdir']/'theme.json',theme,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def _harmony(base,kind):
    r,g,b=core.hex_to_rgb(base)
    import colorsys
    h,l,s=colorsys.rgb_to_hls(r/255,g/255,b/255)
    offsets={'complementary':[0,0.5],'analogous':[0,0.08,0.92],'triadic':[0,0.333,0.667],'split':[0,0.42,0.58],'monochrome':[0,0,0]}.get(kind,[0,0.5])
    out=[]
    for i,off in enumerate(offsets):
        ll=l if kind!='monochrome' else max(0.15,min(0.85,l+(i-1)*0.2))
        rr,gg,bb=colorsys.hls_to_rgb((h+off)%1,ll,s); out.append(core.rgb_to_hex([rr*255,gg*255,bb*255]))
    return out

def family_theme(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; themes=ctx['themes']; report={'preset':preset}
    if preset in {'light-and-dark-theme-designer','theme-variation-generator','seasonal-theme-designer'}:
        names=['neutral','dark','high-contrast','warm','colorblind-safe'] if preset!='seasonal-theme-designer' else ['warm','neutral','dark']
        report['variants']={n:themes[n] for n in names if n in themes}; deck['theme']=p.get('theme',names[0])
    elif preset=='color-blind-safe-palette-designer': deck['theme']='colorblind-safe'
    elif preset in {'color-palette-architect','color-harmony-specialist','campaign-visual-system-designer'}:
        base=p.get('accent','#2563EB'); kind=p.get('harmony','triadic'); palette=_harmony(base,kind)
        theme=copy.deepcopy(themes.get('neutral')); theme['colors']['accent']=palette[0]; theme['colors']['accent2']=palette[1%len(palette)]; theme['name']='Custom harmony'
        deck['theme']=theme; report['palette']=palette
    elif preset=='contrast-ratio-auditor':
        report['audit']=core.audit_deck(deck,ctx['config'],themes,checks=['accessibility','brand'])
    elif preset=='gradient-and-duotone-designer':
        a=p.get('from','#2563EB'); b=p.get('to','#0F766E')
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient></defs><rect width="1280" height="720" fill="url(#g)"/></svg>'
        report['gradient_svg']=_write_text(ctx['outdir']/'asset-gradient.svg',svg,ctx['config'])
    elif preset=='semantic-color-system-designer':
        theme=core.resolve_theme(deck,themes); report['tokens']={k:theme['colors'][k] for k in ['success','warning','danger','accent','text','background']}
    elif preset in {'brand-theme-translator','cross-platform-theme-mapper','corporate-brand-compliance-reviewer'}:
        theme=core.resolve_theme(deck,themes); report['tokens']=theme; report['mapping']={'pptx':'theme1.xml','google_slides':'theme colors','canva':'brand kit','figma':'variables'}
    else:
        deck['theme']=p.get('theme',deck.get('theme','neutral'))
    theme=core.resolve_theme(deck,themes)
    report['contrast']={k:core.contrast_ratio(v,theme['colors']['background']) for k,v in theme['colors'].items()}
    arts=[_write_json(ctx['outdir']/'theme-report.json',report,ctx['config']),_write_json(ctx['outdir']/'theme.json',theme,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

ICONS={
 'arrow':'M4 12h14m-6-6 6 6-6 6','check':'M4 12l5 5 11-11','chart':'M4 20V10m6 10V4m6 16v-7m4 7H2','people':'M8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm8 1a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5ZM2 20c0-4 3-6 6-6s6 2 6 6m2 0c0-3 2-5 4-5s4 2 4 5','lock':'M6 11V8a6 6 0 0 1 12 0v3M5 11h14v10H5z','globe':'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0 0c3-3 3-15 0-18m0 18c-3-3-3-15 0-18M3 12h18','clock':'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 3','bulb':'M9 18h6m-5 3h4m-6-8a6 6 0 1 1 8 0c-1 1-1 2-1 3H9c0-1 0-2-1-3Z'
}
def _svg_icon(name,path,theme,size=64):
    c=theme['colors']['accent']
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" role="img" aria-labelledby="title"><title id="title">{name} icon</title><path d="{path}" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'

def family_vector(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; theme=core.resolve_theme(deck,ctx['themes']); arts=[]; report={'preset':preset}
    names=p.get('icons') or list(ICONS)
    if preset in {'custom-icon-set-designer','pictogram-system-designer','diagram-shape-library-curator','icon-accessibility-reviewer'}:
        (ctx['outdir']/'icons').mkdir(exist_ok=True)
        report['icons']=[]
        for n in names[:24]:
            key=n if n in ICONS else 'check'; svg=_svg_icon(n,ICONS[key],theme)
            if preset=='icon-accessibility-reviewer': report.setdefault('accessibility',[]).append({'icon':n,'has_title':True,'decorative':False})
            arts.append(_write_text(ctx['outdir']/'icons'/f'{core.slugify(n)}.svg',svg,ctx['config'])); report['icons'].append(n)
    elif preset in {'vector-path-editor','vector-export-optimizer','svg-asset-specialist'}:
        path=p.get('path','M10 10 H 90 V 90 H 10 Z')
        if preset=='vector-export-optimizer': path=re.sub(r'\s+',' ',path).strip()
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="512" height="512"><title>Editable vector</title><path d="{path}" fill="{theme["colors"]["accent"]}" stroke="{theme["colors"]["text"]}" stroke-width="2"/></svg>'
        arts.append(_write_text(ctx['outdir']/'asset-vector.svg',svg,ctx['config'])); report['path']=path
    elif preset=='boolean-shape-operations-specialist':
        op=p.get('operation','union')
        if op=='intersection': body='<circle cx="40" cy="50" r="30" fill="'+theme['colors']['accent']+'"/><circle cx="60" cy="50" r="30" fill="'+theme['colors']['accent2']+'" style="mix-blend-mode:multiply"/>'
        elif op=='subtract': body='<defs><mask id="m"><rect width="100" height="100" fill="white"/><circle cx="60" cy="50" r="25" fill="black"/></mask></defs><circle cx="40" cy="50" r="30" fill="'+theme['colors']['accent']+'" mask="url(#m)"/>'
        else: body='<circle cx="40" cy="50" r="30" fill="'+theme['colors']['accent']+'"/><circle cx="60" cy="50" r="30" fill="'+theme['colors']['accent2']+'"/>'
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="512" height="512"><title>{op} composition</title>{body}</svg>'
        arts.append(_write_text(ctx['outdir']/'asset-boolean.svg',svg,ctx['config'])); report['operation']=op; report['note']='Visual boolean composition via SVG group/mask; not a flattened path union.'
    else:
        kind=preset
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><title>{kind}</title><rect width="1280" height="720" fill="{theme["colors"]["background"]}"/>'
        for i in range(8):
            x=80+i*150; y=180+(i%3)*120
            svg+=f'<rect x="{x}" y="{y}" width="120" height="120" rx="{theme.get("radius",12)}" fill="{theme["colors"]["accent"] if i%2 else theme["colors"]["accent2"]}" opacity="0.85"/>'
        svg+='</svg>'
        arts.append(_write_text(ctx['outdir']/'asset-shape-language.svg',svg,ctx['config'])); report['style']=kind
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def _source_image(ctx):
    p=ctx['params'].get('image')
    if not p:
        for s in ctx['deck']['slides']:
            if s.get('image',{}).get('path'): p=s['image']['path']; break
    if not p:
        for a in ctx['deck'].get('assets',[]):
            if a.get('path','').lower().endswith(('.png','.jpg','.jpeg','.webp')): p=a['path']; break
    if not p: p=ctx['config']['paths']['example_image']
    return core.resolve_resource(p)

def family_image(ctx):
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    preset=ctx['entry']['preset']; p=ctx['params']; src=_source_image(ctx); report={'preset':preset,'source':_rel(src)}
    im=Image.open(src); im.load()
    if im.width*im.height>ctx['config']['limits']['max_image_pixels']: raise core.ToolError('IMAGE_TOO_LARGE','Image exceeds pixel limit.',{'pixels':im.width*im.height})
    im=im.convert('RGBA'); report['original']={'width':im.width,'height':im.height,'mode':im.mode}
    out=im.copy()
    if preset in {'image-crop-and-focal-point-specialist','presentation-image-art-director','photo-selection-curator'}:
        w,h=out.size; tw=int(p.get('width',w*0.8)); th=int(p.get('height',h*0.8)); fx=float(p.get('focal_x',0.5)); fy=float(p.get('focal_y',0.5))
        left=int((w-tw)*fx); top=int((h-th)*fy); out=out.crop((max(0,left),max(0,top),min(w,left+tw),min(h,top+th)))
    if preset=='background-removal-specialist':
        key=p.get('key_color'); tol=int(p.get('tolerance',40))
        if key:
            kr,kg,kb=core.hex_to_rgb(key); px=out.load()
            for y in range(out.height):
                for x in range(out.width):
                    r,g,b,a=px[x,y]
                    if abs(r-kr)+abs(g-kg)+abs(b-kb)<tol: px[x,y]=(r,g,b,0)
            report['method']='chroma-key'
        else: report['method']='none'; report['note']='Provide params.key_color for deterministic chroma-key removal; AI segmentation is not bundled.'
    if preset=='image-masking-designer':
        mask=Image.new('L',out.size,0); from PIL import ImageDraw
        d=ImageDraw.Draw(mask); d.ellipse([out.width*0.1,out.height*0.1,out.width*0.9,out.height*0.9],fill=255); out.putalpha(mask)
    if preset in {'photo-retouching-specialist','image-color-grading-designer'}:
        out=ImageEnhance.Contrast(out).enhance(float(p.get('contrast',1.1))); out=ImageEnhance.Color(out).enhance(float(p.get('saturation',1.05))); out=ImageEnhance.Sharpness(out).enhance(float(p.get('sharpness',1.1)))
    if preset=='image-compression-specialist':
        pass
    if preset=='transparent-asset-optimizer':
        out=out.quantize(colors=256).convert('RGBA')
    if preset=='before-and-after-visual-designer':
        comp=Image.new('RGBA',(out.width*2,out.height)); comp.paste(im,(0,0)); comp.paste(out,(out.width,0)); out=comp
    if preset in {'composite-image-builder','product-mockup-compositor'}:
        bg=Image.new('RGBA',(out.width,out.height),p.get('background','#F4F6F8')); bg.alpha_composite(out); out=bg
    if preset=='image-resolution-and-dpi-reviewer':
        dpi=im.info.get('dpi',(96,96)); report['dpi']=dpi; report['print_inches']=[round(im.width/dpi[0],2),round(im.height/dpi[1],2)]; report['flag']=dpi[0]<150
    if preset in {'stock-image-licensing-coordinator','photo-caption-and-credit-manager'}:
        report['rights']={'license':p.get('license','unknown'),'credit':p.get('credit',''),'alt':p.get('alt',''),'review_required':p.get('license') is None}
    fmt='JPEG' if preset=='image-compression-specialist' else 'PNG'
    dest=ctx['outdir']/('processed.'+('jpg' if fmt=='JPEG' else 'png'))
    if fmt=='JPEG': out.convert('RGB').save(dest,quality=int(p.get('quality',82)),optimize=True)
    else: out.save(dest,optimize=True)
    report['output']={'width':out.width,'height':out.height,'bytes':dest.stat().st_size,'format':fmt}
    deck=copy.deepcopy(ctx['deck']); deck['slides'].append({'id':'processed-image','layout':'image','title':preset.replace('-',' ').title(),'image':{'path':_rel(dest),'alt':p.get('alt','Processed image'),'fit':'contain','credit':p.get('credit',''),'license':p.get('license','')},'duration_seconds':30})
    arts=[_rel(dest),_write_json(ctx['outdir']/'image-report.json',report,ctx['config'])]
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_illustration(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; theme=core.resolve_theme(deck,ctx['themes']); c=theme['colors']
    seed=int(p.get('seed',7)); import random
    rnd=random.Random(seed); shapes=[]
    for i in range(int(p.get('elements',18))):
        x=rnd.randint(0,1200); y=rnd.randint(0,640); w=rnd.randint(40,220); h=rnd.randint(40,220); col=rnd.choice([c['accent'],c['accent2'],c['success'],c['warning']])
        if preset in {'paper-cut-style-designer','collage-art-designer'}:
            shapes.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rnd.randint(0,30)}" fill="{col}" opacity="0.8"/>')
        elif preset in {'geometric-art-designer','abstract-background-designer'}:
            shapes.append(f'<circle cx="{x}" cy="{y}" r="{w//2}" fill="{col}" opacity="0.65"/>')
        else:
            shapes.append(f'<path d="M{x} {y} q {w//2} {-h//2} {w} 0 t {w} 0" stroke="{col}" stroke-width="{rnd.randint(3,10)}" fill="none" opacity="0.8"/>')
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720" role="img"><title>{preset.replace("-"," ")}</title><rect width="1280" height="720" fill="{c["background"]}"/>{"".join(shapes)}</svg>'
    plan={'preset':preset,'style_notes':['Keep a limited palette.','Use consistent stroke and corner language.','Provide alt text and licensing before publication.'],'prompt':p.get('prompt') or f'Create a {preset.replace("-"," ")} illustration for {deck.get("title")} using palette {c["accent"]}, {c["accent2"]}.','generative_model_called':False}
    arts=[_write_text(ctx['outdir']/'asset-illustration.svg',svg,ctx['config']),_write_json(ctx['outdir']/'art-direction.json',plan,ctx['config'])]
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,plan,arts+res['artifacts'],res['warnings'])

CHART_TYPES={'bar-and-column-chart-designer':'column','line-and-area-chart-designer':'line','scatter-and-bubble-plot-designer':'scatter','waterfall-chart-designer':'waterfall','funnel-chart-designer':'funnel','heatmap-designer':'heatmap','map-visualization-designer':'map','small-multiples-designer':'small-multiples','kpi-scorecard-designer':'kpi','uncertainty-visualization-specialist':'uncertainty'}
def family_chart(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    slide=next((s for s in deck['slides'] if s.get('chart')),None)
    if slide is None:
        slide={'id':'chart-1','layout':'chart','title':'Data visualization','duration_seconds':45,'chart':{'type':'column','categories':['A','B','C'],'series':[{'name':'Example','values':[3,5,2]}],'source':'Illustrative example','zero_baseline':True}}; deck['slides'].append(slide)
    chart=slide['chart']
    if p.get('chart'): chart.update(p['chart'])
    if preset in CHART_TYPES: chart['type']=CHART_TYPES[preset]
    if preset=='chart-selection-advisor':
        n=len(chart.get('categories') or []); m=len(chart.get('series') or [])
        chart['recommended_type']='line' if n>6 else 'column' if m<=2 else 'small-multiples'
        if preset=='chart-selection-advisor': chart['type']=chart['recommended_type']
    if preset in {'data-to-story-visualizer','chart-annotation-specialist','executive-dashboard-slide-designer'}:
        vals=[v for s in chart.get('series',[]) for v in s.get('values',[]) if isinstance(v,(int,float))]
        if vals and not chart.get('annotations'): chart['annotations']=[{'index':vals.index(max(vals)),'text':'Peak value'}]
        if preset=='executive-dashboard-slide-designer':
            slide['layout']='dashboard'; slide['kpis']=[{'label':s.get('name',f'Metric {i+1}'),'value':(s.get('values') or [0])[-1]} for i,s in enumerate(chart.get('series',[])[:3])]
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'],checks=['charts','accessibility'])
    report={'preset':preset,'chart_type':chart.get('type'),'audit':audit,'story':slide.get('title'),'source':chart.get('source')}
    arts=[_write_json(ctx['outdir']/'chart-spec.json',chart,ctx['config']),_write_json(ctx['outdir']/'chart-report.json',report,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

TABLE_PRESETS={'comparison-matrix-builder':'comparison','feature-comparison-designer':'comparison','pricing-table-designer':'pricing','raci-matrix-builder':'raci','risk-matrix-builder':'risk','prioritization-matrix-builder':'priority','decision-matrix-builder':'decision','swot-layout-designer':'swot','timeline-table-designer':'timeline','financial-statement-slide-formatter':'financial'}
def family_table(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    slide=next((s for s in deck['slides'] if s.get('table')),None)
    if slide is None:
        slide={'id':'table-1','layout':'table','title':'Structured information','duration_seconds':45,'table':{'headers':['Item','Value','Status'],'rows':[['Example','1','Ready']],'caption':'Illustrative table'}}; deck['slides'].append(slide)
    table=slide['table']
    if p.get('table'): table.update(p['table'])
    kind=TABLE_PRESETS.get(preset,'general'); table['kind']=kind
    if preset=='long-table-pagination-specialist':
        rows=table.get('rows') or []; size=int(p.get('page_size',12)); pages=[]
        for i in range(0,len(rows),size):
            pages.append({'id':f"{slide['id']}-page-{i//size+1}",'layout':'table','title':f"{slide.get('title','Table')} ({i//size+1}/{math.ceil(len(rows)/size)})",'table':{'headers':table.get('headers',[]),'rows':rows[i:i+size],'caption':table.get('caption','')}})
        if pages:
            idx=deck['slides'].index(slide); deck['slides'][idx:idx+1]=pages; slide=pages[0]
    if preset=='conditional-formatting-designer':
        table['conditional_formatting']={'rule':'numeric','threshold':p.get('threshold',0),'above':ctx['themes'].get('neutral',{}).get('colors',{}).get('success','#15803D'),'below':ctx['themes'].get('neutral',{}).get('colors',{}).get('danger','#B91C1C')}
    if preset=='table-to-visual-converter':
        headers=table.get('headers') or []; rows=table.get('rows') or []
        numeric=[]
        for j in range(len(headers)):
            try: vals=[float(r[j]) for r in rows]; numeric.append((j,vals))
            except Exception: pass
        if numeric:
            j,vals=numeric[0]; slide['layout']='chart'; slide['chart']={'type':'column','categories':[str(r[0]) for r in rows],'series':[{'name':headers[j],'values':vals}],'source':'Converted from table','zero_baseline':True}
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'],checks=['accessibility','content'])
    report={'preset':preset,'kind':kind,'rows':len(table.get('rows') or []),'columns':len(table.get('headers') or []),'audit':audit}
    arts=[_write_json(ctx['outdir']/'table-spec.json',table,ctx['config']),_write_json(ctx['outdir']/'table-report.json',report,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

DIAGRAM_TYPES={'flowchart-designer':'flow','process-map-architect':'process','swimlane-diagram-designer':'swimlane','system-architecture-diagram-designer':'architecture','network-diagram-designer':'network','mind-map-designer':'mindmap','concept-map-designer':'concept','causal-loop-diagram-designer':'causal','funnel-process-designer':'flow','cycle-diagram-designer':'cycle','pyramid-diagram-designer':'pyramid','venn-diagram-designer':'venn','hierarchy-and-org-chart-designer':'hierarchy','journey-map-designer':'journey','service-blueprint-designer':'blueprint'}
def family_diagram(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    slide=next((s for s in deck['slides'] if s.get('diagram')),None)
    if slide is None:
        slide={'id':'diagram-1','layout':'diagram','title':'Process diagram','duration_seconds':45,'diagram':{'type':'flow','nodes':[{'id':'a','label':'Start'},{'id':'b','label':'Step'},{'id':'c','label':'End'}],'edges':[{'from':'a','to':'b'},{'from':'b','to':'c'}]}}; deck['slides'].append(slide)
    d=slide['diagram']
    if p.get('diagram'): d.update(p['diagram'])
    d['type']=DIAGRAM_TYPES.get(preset,d.get('type','flow'))
    ids={n['id'] for n in d.get('nodes',[])}; broken=[e for e in d.get('edges',[]) if e.get('from') not in ids or e.get('to') not in ids]
    report={'preset':preset,'type':d['type'],'nodes':len(d.get('nodes',[])),'edges':len(d.get('edges',[])),'broken_edges':broken}
    arts=[_write_json(ctx['outdir']/'diagram-spec.json',d,ctx['config']),_write_json(ctx['outdir']/'diagram-report.json',report,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def _critical_path(events):
    by={e.get('id') or e.get('label'):e for e in events}
    def dur(e):
        try:
            a=datetime.fromisoformat(e.get('start') or e.get('date')); b=datetime.fromisoformat(e.get('end') or e.get('start') or e.get('date'))
            return max(1,(b-a).days)
        except Exception:
            return 1
    memo={}; visiting=set()
    def path(k):
        if k in memo: return memo[k]
        if k in visiting: return (0,[])
        visiting.add(k)
        e=by.get(k,{})
        deps=[d for d in (e.get('dependencies') or []) if d in by]
        best=(0,[])
        for d in deps:
            cand=path(d)
            if cand[0]>best[0]: best=cand
        memo[k]=(best[0]+dur(e),best[1]+[k]); visiting.remove(k); return memo[k]
    if not by: return {'days':0,'path':[]}
    k=max(by,key=lambda k:path(k)[0])
    return {'days':path(k)[0],'path':path(k)[1]}

def family_timeline(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    slide=next((s for s in deck['slides'] if s.get('timeline')),None)
    if slide is None:
        slide={'id':'timeline-1','layout':'timeline','title':'Roadmap','duration_seconds':45,'timeline':{'mode':'gantt','events':[{'label':'Phase 1','start':'2026-01-01','end':'2026-03-31','status':'active'},{'label':'Phase 2','start':'2026-04-01','end':'2026-06-30','status':'planned'}]}}; deck['slides'].append(slide)
    t=slide['timeline']
    if p.get('timeline'): t.update(p['timeline'])
    mode={'project-timeline-designer':'timeline','product-roadmap-designer':'roadmap','milestone-map-designer':'milestones','gantt-slide-designer':'gantt','release-plan-visualizer':'gantt','transformation-roadmap-designer':'roadmap','strategic-horizon-map-designer':'horizon','quarterly-plan-designer':'gantt','event-program-timeline-designer':'timeline','historical-timeline-designer':'timeline','dependency-map-designer':'gantt','critical-path-visualizer':'gantt','now-next-later-designer':'now-next-later','phased-rollout-designer':'roadmap','roadmap-status-update-designer':'roadmap'}.get(preset,t.get('mode','timeline'))
    t['mode']=mode
    cp=_critical_path(t.get('events') or []) if preset in {'critical-path-visualizer','dependency-map-designer'} else None
    report={'preset':preset,'mode':mode,'events':len(t.get('events') or []),'critical_path':cp}
    arts=[_write_json(ctx['outdir']/'timeline-spec.json',t,ctx['config']),_write_json(ctx['outdir']/'timeline-report.json',report,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_motion(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    kind=p.get('transition','fade'); dur=int(p.get('duration_ms',450))
    if preset in {'transition-system-designer','cinematic-slide-transition-designer'}: kind=p.get('transition','fade')
    if preset=='motion-accessibility-reviewer': kind='none'; dur=0
    storyboard=[]
    for i,s in enumerate(deck['slides']):
        s['transition']={'type':kind,'duration_ms':dur}
        storyboard.append({'slide':s['id'],'order':i+1,'trigger':'click' if not p.get('auto') else 'after-previous','effect':kind,'duration_ms':dur,'build':[{'object':o.get('id',f'object-{j}'),'effect':p.get('build_effect','fade'),'delay_ms':j*int(p.get('stagger_ms',150))} for j,o in enumerate(s.get('objects') or [])]})
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'],checks=['motion','accessibility'])
    report={'preset':preset,'transition':kind,'duration_ms':dur,'storyboard':storyboard,'audit':audit,'native_pptx_object_animation':False,'portable_preview':'HTML/GIF'}
    arts=[_write_json(ctx['outdir']/'motion-storyboard.json',report,ctx['config'])]
    formats=list(dict.fromkeys(ctx['formats']+(['gif'] if 'gif' in ctx['params'].get('extra_formats',[]) else [])))
    res=_emit(ctx,deck,formats=formats,motion=True)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def _srt_time(seconds):
    ms=int(seconds*1000); h=ms//3600000; m=(ms%3600000)//60000; s=(ms%60000)//1000; mm=ms%1000
    return f'{h:02d}:{m:02d}:{s:02d},{mm:03d}'

def family_media(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    script=[]; srt=[]; vtt=['WEBVTT','']; t=0.0
    for i,s in enumerate(deck['slides'],1):
        notes=s.get('notes') or s.get('subtitle') or s.get('title','')
        dur=float(s.get('duration_seconds',30)); script.append({'slide':s['id'],'text':notes,'duration_seconds':dur})
        srt.append(f'{i}\n{_srt_time(t)} --> {_srt_time(t+dur)}\n{notes}\n'); vtt.append(f'{_srt_time(t).replace(",",".")} --> {_srt_time(t+dur).replace(",",".")}\n{notes}\n'); t+=dur
    credits=[{'asset':a.get('id'),'license':a.get('license','unknown'),'credit':a.get('credit','')} for a in deck.get('assets',[])]
    plan={'preset':preset,'voiceover_script':script,'captions':{'srt':True,'vtt':True},'credits':credits,'media':deck.get('media',{}),'recording_performed':False,'video_encoding_performed':False,'audio_mixing_performed':False,'chapters':[{'slide':s['id'],'start_seconds':sum(x.get('duration_seconds',30) for x in deck['slides'][:i])} for i,s in enumerate(deck['slides'])],'compatibility_notes':['Embed local media only after licensing and playback checks.','MP4/H.264 and MP3/AAC are common portable choices; verify in the target presenter.']}
    arts=[_write_json(ctx['outdir']/'media-plan.json',plan,ctx['config']),_write_text(ctx['outdir']/'voiceover-script.md',_md('Voiceover script',[(s['slide'],s['text']) for s in script]),ctx['config']),_write_text(ctx['outdir']/'captions.srt','\n'.join(srt),ctx['config']),_write_text(ctx['outdir']/'captions.vtt','\n'.join(vtt),ctx['config'])]
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,plan,arts+res['artifacts'],res['warnings'])

def family_interactive(ctx):
    import segno
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    ids=[s['id'] for s in deck['slides']]
    if preset in {'hyperlinked-deck-architect','nonlinear-navigation-usability-tester','interactive-dashboard-navigator'}:
        for i,s in enumerate(deck['slides']):
            if i+1<len(ids): s.setdefault('link',{'target_slide':ids[i+1],'label':'Next'})
        deck['slides'][-1].setdefault('link',{'target_slide':ids[0],'label':'Start over'})
    if preset in {'interactive-quiz-builder','branching-scenario-designer','choose-your-path-story-designer','escape-room-presentation-designer','presentation-game-designer'}:
        quiz_slide=next((s for s in deck['slides'] if s.get('quiz')),None)
        if quiz_slide is None:
            quiz_slide={'id':'quiz-1','layout':'quiz','title':'Check understanding','quiz':{'question':'Choose the next step','options':[{'text':'Continue','correct':True,'feedback':'Correct.','next':ids[-1]},{'text':'Review','correct':False,'feedback':'Review the evidence.','next':ids[0]}]},'duration_seconds':30}; deck['slides'].append(quiz_slide)
    if preset in {'qr-connected-presentation-designer'}:
        url=p.get('url') or 'https://example.com/presentation'
        if not re.match(r'^https?://',url): raise core.ToolError('INVALID_URL','QR URL must be http(s).')
        qr=segno.make(url,error='m'); qr.save(ctx['outdir']/'asset-qr.svg',scale=8,border=2)
        deck['slides'].append({'id':'qr','layout':'image','title':'Connect','image':{'path':_rel(ctx['outdir']/'asset-qr.svg'),'alt':'QR code linking to '+url,'fit':'contain'},'duration_seconds':20})
    if preset in {'touchscreen-kiosk-designer'}:
        for s in deck['slides']: s.setdefault('transition',{'type':'fade','duration_ms':300})
    nav={'preset':preset,'slides':ids,'links':[{'from':s['id'],'to':(s.get('link') or {}).get('target_slide')} for s in deck['slides'] if s.get('link')],'quiz_count':sum(1 for s in deck['slides'] if s.get('quiz')),'broken_links':[l for l in [{'from':s['id'],'to':(s.get('link') or {}).get('target_slide')} for s in deck['slides'] if s.get('link')] if l['to'] not in ids],'external_embeds_allowed':False,'note':'Embedded web content, AR/VR, and 360 runtimes are not bundled; HTML navigation and quizzes are local.'}
    arts=[_write_json(ctx['outdir']/'interaction-report.json',nav,ctx['config'])]
    res=_emit(ctx,deck,formats=list(dict.fromkeys(ctx['formats']+['html'])),kiosk=preset=='touchscreen-kiosk-designer',motion=True)
    return _base_result(ctx,nav,arts+res['artifacts'],res['warnings'])

GENRE_CHECKLISTS={
 'board-meeting-deck-designer':['Decision requested','Financial evidence','Risk','Appendix'],
 'quarterly-business-review-designer':['KPIs','Variance','Drivers','Actions'],
 'investor-pitch-deck-designer':['Problem','Solution','Market','Traction','Team','Ask'],
 'lesson-slide-designer':['Objective','Example','Practice','Assessment'],
 'thesis-defense-deck-designer':['Question','Method','Results','Limits','Contribution'],
 'keynote-presentation-designer':['Hook','Story','Evidence','Close'],
 'pechakucha-deck-designer':['20 slides','20 seconds each'],
 'ignite-talk-designer':['20 slides','15 seconds each'],
}
def family_genre(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    checklist=GENRE_CHECKLISTS.get(preset,['Audience','Outcome','Evidence','Decision','Next step'])
    if preset=='pechakucha-deck-designer':
        deck['slides']=deck['slides'][:20]
        for s in deck['slides']: s['duration_seconds']=20; s['transition']={'type':'fade','duration_ms':300}
    if preset=='ignite-talk-designer':
        deck['slides']=deck['slides'][:20]
        for s in deck['slides']: s['duration_seconds']=15; s['transition']={'type':'fade','duration_ms':250}
    if preset in {'startup-one-pager-visual-designer','pitch-poster-designer','printable-presentation-handout-designer'}:
        ctx['variant']='poster' if 'poster' in preset else 'handout'
    if preset in {'social-media-carousel-designer','digital-magazine-deck-designer','presentation-comic-designer','storyboard-deck-designer'}:
        ctx['variant']='magazine' if 'magazine' in preset else 'comic' if 'comic' in preset else 'storyboard'
    report={'preset':preset,'checklist':checklist,'missing':[c for c in checklist if not any(c.lower() in core.slide_text(s).lower() for s in deck['slides'])],'slide_count':len(deck['slides'])}
    arts=[_write_json(ctx['outdir']/'genre-report.json',report,ctx['config']),_write_text(ctx['outdir']/'genre-checklist.md',_md('Genre checklist',[('Required',checklist),('Missing',report['missing'])]),ctx['config'])]
    res=_emit(ctx,deck,variant=ctx['variant'],handout='handout' in preset)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_accessibility(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    if preset in {'plain-language-editor','cognitive-load-reviewer'}:
        for s in deck['slides']:
            for key in ('title','subtitle','body'):
                if s.get(key): s[key],notes=core.plain_language(s[key])
            if s.get('bullets'): s['bullets']=[core.plain_language(b)[0] for b in s['bullets']]
    if preset in {'dyslexia-friendly-slide-designer','low-vision-presentation-designer','neuroinclusive-presentation-designer','accessible-learning-materials-designer'}:
        deck['theme']='high-contrast'
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'])
    if preset in {'accessibility-remediation-specialist','ai-accessibility-remediation-assistant','alt-text-writer','reading-order-specialist'} or p.get('remediate',True):
        deck,changes=core.repair_deck(deck,ctx['config'],ctx['themes'],audit)
    else: changes=[]
    audit_after=core.audit_deck(deck,ctx['config'],ctx['themes'])
    report={'preset':preset,'before':audit,'after':audit_after,'changes':changes,'reading_order':[s['id'] for s in deck['slides']],'wcag_note':'Automated checks cover contrast, alt text, tables, motion, and content density; manual screen-reader and keyboard testing is still required.'}
    arts=[_write_json(ctx['outdir']/'accessibility-report.json',report,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_localization(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; locale=p.get('locale','fr')
    translations=p.get('translations') or {}
    if not translations:
        try: translations=core.load_json(core.resolve_resource(ctx['config']['paths']['example_translations']),1024*1024)
        except Exception: translations={}
    strings=[]
    for s in deck['slides']:
        for key in ('title','subtitle','body'):
            if s.get(key): strings.append({'slide':s['id'],'key':key,'source':s[key]})
        for i,b in enumerate(s.get('bullets') or []): strings.append({'slide':s['id'],'key':f'bullets[{i}]','source':b})
    applied=0; missing=[]
    locmap=translations.get(locale,{})
    for s in deck['slides']:
        tr=s.setdefault('translations',{}).setdefault(locale,{})
        for key in ('title','subtitle','body'):
            if s.get(key):
                if s[key] in locmap: tr[key]=locmap[s[key]]; applied+=1
                else: missing.append({'slide':s['id'],'key':key,'source':s[key]})
                if p.get('apply',True) and s[key] in locmap: s[key]=locmap[s[key]]
    deck['language']=locale
    if locale in {'ar','he','fa','ur'}: deck['direction']='rtl'
    expansion=[{'slide':s['id'],'ratio':round(len(core.slide_text(s))/max(1,len(core.slide_text(next((x for x in ctx['deck']['slides'] if x['id']==s['id']),s)))),2)} for s in deck['slides']]
    report={'preset':preset,'locale':locale,'direction':deck['direction'],'strings':len(strings),'applied':applied,'missing':missing,'expansion':expansion,'date_format':ctx['config'].get('locales',{}).get(locale,{}).get('date','%Y-%m-%d'),'number_example':core.format_number(12345.67,locale,ctx['config']),'machine_translation_called':False,'cultural_review_required':True}
    arts=[_write_json(ctx['outdir']/'localization-report.json',report,ctx['config']),_write_json(ctx['outdir']/'strings.json',strings,ctx['config']),_write_json(ctx['outdir']/'translations.json',translations,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_governance(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    meta=deck.get('metadata',{})
    policy={'preset':preset,'owner':p.get('owner',meta.get('owner','Design Operations')),'version':p.get('version',meta.get('version','1.0')),'naming':'{project}-{audience}-v{version}-{status}','review_states':['draft','review','approved','published'],'approval_required_for':['publish','external sharing','brand exception','license change'],'template_rules':['Use approved theme tokens.','Keep editable masters/layouts.','Record asset licenses.','Run accessibility and QA before delivery.'],'assets':deck.get('assets',[]),'metrics':{'slide_count':len(deck['slides']),'asset_count':len(deck.get('assets',[])),'licensed_assets':sum(1 for a in deck.get('assets',[]) if a.get('license'))}}
    arts=[_write_json(ctx['outdir']/'governance-policy.json',policy,ctx['config']),_write_text(ctx['outdir']/'governance.md',_md('Design governance',[('Owner',policy['owner']),('Naming',policy['naming']),('Rules',policy['template_rules']),('Metrics',policy['metrics'])]),ctx['config'])]
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,policy,arts+res['artifacts'],res['warnings'])

def family_collaboration(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']; review=deck.setdefault('review',{})
    comments=review.setdefault('comments',[]); approvals=review.setdefault('approvals',[])
    triage=[{'id':c.get('id',f'c{i}'),'slide':c.get('slide_id'),'severity':c.get('severity','minor'),'status':c.get('status','open'),'action':'fix' if c.get('severity') in {'critical','major'} else 'review'} for i,c in enumerate(comments,1)]
    if preset in {'final-sign-off-coordinator','approval-workflow-designer'}:
        if preset=='final-sign-off-coordinator':
            if not ctx['approve']: raise core.ToolError('APPROVAL_REQUIRED','Final sign-off requires --approve.',{},core.EXIT_APPROVAL)
            approvals.append({'role':p.get('role','owner'),'approver':p.get('approver','human-reviewer'),'timestamp':datetime.utcnow().isoformat()+'Z','scope':'deck','fingerprint':core.fingerprint(deck)})
            deck.setdefault('metadata',{})['status']='approved'
    workflow={'preset':preset,'reviewers':p.get('reviewers',['content-owner','design-lead','accessibility-reviewer']),'states':['draft','review','approved','published'],'comments':triage,'approvals':approvals,'deadlines':p.get('deadlines',[]),'conflicts':[],'change_log':review.get('changes',[]),'signoff_recorded':bool(approvals)}
    arts=[_write_json(ctx['outdir']/'collaboration-plan.json',workflow,ctx['config']),_write_text(ctx['outdir']/'review-workflow.md',_md('Review workflow',[('Reviewers',workflow['reviewers']),('Comment triage',triage),('Approvals',approvals)]),ctx['config'])]
    res=_emit(ctx,deck,formats=[f for f in ctx['formats'] if f in {'json','svg','html','pptx'}] or ['json'])
    return _base_result(ctx,workflow,arts+res['artifacts'],res['warnings'])

def family_qa(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    if preset=='presentation-recovery-and-corruption-specialist' and ctx.get('input_kind')=='pptx':
        deck,report= pptx.recover_pptx(core.safe_output_path(ctx['input_path'],must_exist=True),ctx['config'])
    audit=core.audit_deck(deck,ctx['config'],ctx['themes'])
    changes=[]
    if p.get('repair') or preset in {'presentation-recovery-and-corruption-specialist','privacy-and-metadata-reviewer'}:
        if preset=='privacy-and-metadata-reviewer':
            if not ctx['approve']: raise core.ToolError('APPROVAL_REQUIRED','Privacy redaction requires --approve.',{},core.EXIT_APPROVAL)
            deck.get('metadata',{}).pop('author',None)
            for s in deck['slides']: s['notes']=re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}','[redacted]',s.get('notes',''))
            changes.append('Redacted author metadata and email-like strings from notes.')
        deck,changes2=core.repair_deck(deck,ctx['config'],ctx['themes'],audit); changes+=changes2
    report={'preset':preset,'audit':audit,'changes':changes,'delivery_checklist':['Editable PPTX','SVG/HTML previews','Accessibility report','License/credit review','Link check','Presenting-device rehearsal'],'fidelity_note':'Cross-platform rendering requires the target application; this tool validates structure and portable artifacts.'}
    arts=[_write_json(ctx['outdir']/'qa-report.json',report,ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_ai(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    prompts={'brief':f'Interpret this brief for {deck.get("audience","the audience")} and propose a decision-oriented outline: {deck.get("purpose","")}','rewrite':'Rewrite slide text in plain language while preserving facts and citations.','layout':'Propose three layout variants that preserve hierarchy and accessibility.','theme':'Propose a brand-safe theme with accessible contrast.','image':f'Create a brand-safe illustration for {deck.get("title")} with alt text and license metadata.','notes':'Generate speaker notes that explain the visual and transition without adding unsupported facts.'}
    data={'preset':preset,'model_called':False,'provider':'local-heuristic','prompts':prompts,'human_review_required':True,'quality':core.audit_deck(deck,ctx['config'],ctx['themes'])}
    if preset in {'ai-slide-content-rewriter','ai-speaker-notes-generator'}:
        for s in deck['slides']:
            if preset=='ai-slide-content-rewriter':
                if s.get('body'): s['body']=core.plain_language(s['body'])[0]
                if s.get('bullets'): s['bullets']=[core.plain_language(b)[0] for b in s['bullets']]
            else: s.setdefault('notes',f"Explain {s.get('title','this slide')}, then connect it to {deck.get('purpose','the outcome')}.")
    if preset=='ai-layout-variation-generator':
        data['variants']=['standard','thirds','minimal']
    if preset=='ai-asset-tagging-and-retrieval-agent':
        data['tags']=[{'asset':a.get('id'),'tags':a.get('tags') or [w.lower() for w in re.findall(r'[A-Za-z]{4,}',a.get('alt',''))[:5]]} for a in deck.get('assets',[])]
    arts=[_write_json(ctx['outdir']/'ai-workflow.json',data,ctx['config']),_write_text(ctx['outdir']/'prompts.md',_md('Prompt set',list(prompts.items())),ctx['config'])]
    res=_emit(ctx,deck)
    return _base_result(ctx,data,arts+res['artifacts'],res['warnings'])

def family_format(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']
    variant='poster' if any(x in preset for x in ['poster','one-pager','lookbook','moodboard']) else 'magazine' if any(x in preset for x in ['magazine','carousel','photo-essay']) else 'comic' if 'comic' in preset else 'storyboard' if 'storyboard' in preset else 'handout' if 'handout' in preset or 'workbook' in preset else ctx['variant']
    if preset=='infographic-presentation-designer':
        for s in deck['slides']:
            if s.get('layout')=='bullets' and len(s.get('bullets') or [])>=3:
                s['layout']='kpi'; s['kpis']=[{'label':b,'value':str(i+1)} for i,b in enumerate(s['bullets'][:4])]
    report={'preset':preset,'variant':variant,'editable_outputs':['pptx','svg','html'],'print_outputs':['pdf','png'] if 'pdf' in ctx['formats'] or 'png' in ctx['formats'] else []}
    arts=[_write_json(ctx['outdir']/'format-report.json',report,ctx['config'])]
    res=_emit(ctx,deck,variant=variant,handout=variant=='handout')
    return _base_result(ctx,report,arts+res['artifacts'],res['warnings'])

def family_pipeline(ctx):
    deck=copy.deepcopy(ctx['deck']); preset=ctx['entry']['preset']; p=ctx['params']
    pipeline={'preset':preset,'input_kind':ctx.get('input_kind'),'primary_entry_point':ctx['config']['paths'].get('capabilities','').replace('asset-capabilities.json','tools-presentation-design.py'),'stages':['validate','normalize','layout','render','audit','package'],'ci':{'command':'python3 universal-presentation-design/tools-presentation-design.py audit --input universal-presentation-design/asset-example-deck.json --fail-on-major','exit_codes':{'ok':0,'validation':2,'approval':3,'timeout':6}},'template_mapping':p.get('mapping',{}),'batch_limit':ctx['config']['limits']['max_batch_items']}
    if preset=='csv-to-slide-automation-designer': pipeline['csv']={'delimiter':',','header_row':True,'numeric_detection':True}
    if preset=='database-driven-presentation-builder': pipeline['database']={'mode':'local-export-only','live_connection':False,'accepted_formats':['csv','json']}
    if preset=='batch-rendering-and-thumbnail-generator': pipeline['thumbnail']={'width':320,'contact_sheet':True}
    arts=[_write_json(ctx['outdir']/'pipeline.json',pipeline,ctx['config']),_write_text(ctx['outdir']/'pipeline.md',_md('Pipeline',[('Stages',pipeline['stages']),('CI',pipeline['ci'])]),ctx['config'])]
    formats=ctx['formats']
    if preset=='batch-rendering-and-thumbnail-generator': formats=list(dict.fromkeys(formats+['png']))
    res=_emit(ctx,deck,formats=formats)
    return _base_result(ctx,pipeline,arts+res['artifacts'],res['warnings'])

FAMILIES={'strategy':family_strategy,'planning':family_planning,'pptx':family_pptx,'handoff':family_handoff,'geometry':family_geometry,'layout':family_layout,'typography':family_typography,'theme':family_theme,'vector':family_vector,'image':family_image,'illustration':family_illustration,'chart':family_chart,'table':family_table,'diagram':family_diagram,'timeline':family_timeline,'motion':family_motion,'media':family_media,'interactive':family_interactive,'genre':family_genre,'accessibility':family_accessibility,'localization':family_localization,'governance':family_governance,'collaboration':family_collaboration,'qa':family_qa,'ai':family_ai,'format':family_format,'pipeline':family_pipeline}

def run_capability(entry,ctx):
    fn=FAMILIES.get(entry['family'])
    if fn is None: raise core.ToolError('UNKNOWN_FAMILY','No handler for capability family.',{'family':entry['family']},core.EXIT_INTERNAL)
    ctx['entry']=entry
    if entry.get('approval_required') and not ctx['approve'] and entry['family']!='handoff':
        raise core.ToolError('APPROVAL_REQUIRED','This capability requires explicit approval.',{'capability':entry['id']},core.EXIT_APPROVAL)
    ctx['deadline'].check('dispatch')
    return fn(ctx)
