"""Editable PowerPoint production, inspection, and recovery via python-pptx + OOXML.

Native PowerPoint application automation, morph, and object-level animation are not claimed.
"""
from __future__ import annotations
import copy, io, json, re, uuid, zipfile
from pathlib import Path
from lxml import etree
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.chart.data import CategoryChartData, XyChartData, BubbleChartData
from pptx.enum.chart import XL_CHART_TYPE
import tools_core as core
import tools_render as render

NS={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main','r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships','p14':'http://schemas.microsoft.com/office/powerpoint/2010/main'}

def _emu(px): return Emu(int(float(px)*914400/96))
def _rgb(v):
    r,g,b=core.hex_to_rgb(v or '#000000'); return RGBColor(r,g,b)

def _set_alt(shape, text):
    try:
        el=shape._element
        cnv=el.find('.//p:cNvPr',NS)
        if cnv is None: cnv=el.find('.//a:cNvPr',NS)
        if cnv is not None: cnv.set('descr',str(text)[:2000])
    except Exception:
        pass

def _add_text(slide,p,theme):
    box=slide.shapes.add_textbox(_emu(p['x']),_emu(p['y']),_emu(p['w']),_emu(max(p['h'],p.get('size',22)*1.2)))
    tf=box.text_frame; tf.word_wrap=True
    tf.margin_left=0; tf.margin_right=0; tf.margin_top=0; tf.margin_bottom=0
    lines=p.get('lines') or render.wrap_text(p.get('text',''),p.get('w',200),p.get('size',22),12)
    for i,line in enumerate(lines):
        para=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        para.text=str(line)
        para.alignment={'center':PP_ALIGN.CENTER,'right':PP_ALIGN.RIGHT}.get(p.get('align','left'),PP_ALIGN.LEFT)
        para.font.size=Pt(p.get('size',22)*0.75); para.font.name=p.get('font',theme['fonts']['body']); para.font.bold=bool(p.get('bold')); para.font.italic=bool(p.get('italic')); para.font.color.rgb=_rgb(p.get('color',theme['colors']['text']))
    _set_alt(box,p.get('text',''))
    return box

def _add_shape(slide,p,theme):
    t=p['type']
    if t=='text': return _add_text(slide,p,theme)
    if t=='line':
        conn=slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,_emu(p['x1']),_emu(p['y1']),_emu(p['x2']),_emu(p['y2']))
        conn.line.color.rgb=_rgb(p.get('stroke',theme['colors']['grid'])); conn.line.width=Pt(p.get('width',1)*0.75)
        if p.get('arrow'):
            ln=conn._element.spPr.find('a:ln',NS)
            if ln is None: ln=etree.SubElement(conn._element.spPr,'{'+NS['a']+'}ln')
            etree.SubElement(ln,'{'+NS['a']+'}tailEnd',{'type':'triangle','w':'med','len':'med'})
        return conn
    if t=='image':
        try:
            src=core.resolve_resource(p.get('path',''))
            pic=slide.shapes.add_picture(str(src),_emu(p['x']),_emu(p['y']),_emu(p['w']),_emu(p['h']))
            _set_alt(pic,p.get('alt',''))
            return pic
        except Exception:
            return _add_text(slide,{**p,'type':'text','text':'Image unavailable: '+p.get('path',''),'size':16,'color':theme['colors']['muted'],'lines':['Image unavailable']},theme)
    shape_type=MSO_SHAPE.OVAL if t=='ellipse' else (MSO_SHAPE.ROUNDED_RECTANGLE if p.get('radius',0)>0 else MSO_SHAPE.RECTANGLE)
    sh=slide.shapes.add_shape(shape_type,_emu(p['x']),_emu(p['y']),_emu(max(1,p['w'])),_emu(max(1,p['h'])))
    sh.fill.solid(); sh.fill.fore_color.rgb=_rgb(p.get('fill',theme['colors']['surface']))
    if p.get('opacity') is not None:
        try: sh.fill.transparency=1-float(p['opacity'])
        except Exception: pass
    if p.get('stroke'):
        sh.line.color.rgb=_rgb(p['stroke'])
    else:
        sh.line.fill.background()
    return sh

def _native_chart(slide,chart,box,theme):
    kind=chart.get('type','column'); series=chart.get('series') or []; cats=chart.get('categories') or []
    if kind in {'bar','column'}:
        data=CategoryChartData(); data.categories=cats
        for s in series: data.add_series(s.get('name','Series'),s.get('values') or [])
        ct=XL_CHART_TYPE.BAR_CLUSTERED if kind=='bar' else XL_CHART_TYPE.COLUMN_CLUSTERED
    elif kind in {'line','area','uncertainty'}:
        data=CategoryChartData(); data.categories=cats
        for s in series: data.add_series(s.get('name','Series'),s.get('values') or [])
        ct=XL_CHART_TYPE.LINE_MARKERS if kind!='area' else XL_CHART_TYPE.AREA
    elif kind in {'scatter','bubble'}:
        if kind=='scatter':
            data=XyChartData()
            for s in series:
                sd=data.add_series(s.get('name','Series'))
                for i,v in enumerate(s.get('values') or []): sd.add_data_point(i+1,v)
            ct=XL_CHART_TYPE.XY_SCATTER
        else:
            data=BubbleChartData()
            for s in series:
                sd=data.add_series(s.get('name','Series'))
                for i,v in enumerate(s.get('values') or []): sd.add_data_point(i+1,v,(s.get('sizes') or [1]*len(s.get('values') or []))[i])
            ct=XL_CHART_TYPE.BUBBLE
    else:
        return None
    gf=slide.shapes.add_chart(ct,_emu(box['x']),_emu(box['y']),_emu(box['w']),_emu(box['h']),data)
    ch=gf.chart
    try:
        ch.has_legend=len(series)>1
        ch.font.size=Pt(12); ch.font.name=theme['fonts']['body']
        if hasattr(ch,'value_axis') and chart.get('zero_baseline',True) and kind in {'bar','column','area'}:
            ch.value_axis.minimum_scale=0
    except Exception:
        pass
    return gf

def _native_table(slide,table,box,theme):
    headers=table.get('headers') or []; rows=table.get('rows') or []
    if not headers and rows: headers=[f'Column {i+1}' for i in range(len(rows[0]))]
    cols=max(1,len(headers)); rcount=len(rows)+1
    gf=slide.shapes.add_table(rcount,cols,_emu(box['x']),_emu(box['y']),_emu(box['w']),_emu(box['h']))
    tbl=gf.table
    for j,h in enumerate(headers):
        cell=tbl.cell(0,j); cell.text=str(h)
        cell.fill.solid(); cell.fill.fore_color.rgb=_rgb(theme['colors']['accent'])
        for p in cell.text_frame.paragraphs:
            p.font.color.rgb=RGBColor(255,255,255); p.font.bold=True; p.font.size=Pt(12)
    for i,row in enumerate(rows,1):
        for j in range(cols):
            cell=tbl.cell(i,j); cell.text=str(row[j-1]) if j-1<len(row) else ''
            cell.fill.solid(); cell.fill.fore_color.rgb=_rgb(theme['colors']['surface'] if i%2 else theme['colors']['background'])
            for p in cell.text_frame.paragraphs:
                p.font.size=Pt(11); p.font.color.rgb=_rgb(theme['colors']['text'])
    return gf

def _transition(slide,slide_def,kiosk=False):
    tr=slide_def.get('transition') or {}
    kind=tr.get('type','none')
    if kind=='none' and not kiosk: return
    el=slide._element
    trans=etree.SubElement(el,'{'+NS['p']+'}transition',{'spd':'med','advClick':'0' if kiosk else '1'})
    if kiosk: trans.set('advTm',str(int(slide_def.get('duration_seconds',10)*1000)))
    if kind in {'fade','push','wipe'}: etree.SubElement(trans,'{'+NS['p']+'}'+kind)

def build_pptx(deck,themes,path,config,*,variant='standard',native=True,kiosk=False,sections=True):
    theme=core.resolve_theme(deck,themes)
    prs=Presentation(); canvas=deck.get('canvas',config['defaults']['canvas'])
    prs.slide_width=_emu(canvas['width']); prs.slide_height=_emu(canvas['height'])
    blank=prs.slide_layouts[6]
    slides=[]; shapes_by_slide=[]
    for i,s in enumerate(deck['slides']):
        slide=prs.slides.add_slide(blank); slides.append(slide)
        laid=render.layout_slide(deck,s,i,theme,variant)
        box=laid['box']; added=[]
        skip_native = native and s.get('layout') in {'chart','table'} and (s.get('chart') or s.get('table'))
        for p in laid['primitives']:
            if skip_native and p.get('role') not in {'background','title','subtitle','page'} and p['type']!='image':
                # skip chart/table body primitives inside content box; keep footer/link outside
                px=p.get('x',p.get('x1',0)); py=p.get('y',p.get('y1',0))
                if box['x']-2<=px<=box['x']+box['w']+2 and box['y']-2<=py<=box['y']+box['h']+2:
                    continue
            sh=_add_shape(slide,p,theme); added.append((sh,p))
        if skip_native:
            if s.get('chart'): _native_chart(slide,s['chart'],box,theme)
            elif s.get('table'): _native_table(slide,s['table'],box,theme)
        if s.get('notes'):
            slide.notes_slide.notes_text_frame.text=s['notes']
        _transition(slide,s,kiosk)
        shapes_by_slide.append(added)
    byid={s['id']:sl for s,sl in zip(deck['slides'],slides)}
    for s,added in zip(deck['slides'],shapes_by_slide):
        for sh,p in added:
            link=p.get('link') or {}
            try:
                if link.get('target_slide') and link['target_slide'] in byid:
                    sh.click_action.target_slide=byid[link['target_slide']]
                elif link.get('url') and re.match(r'^https?://',link['url']):
                    sh.click_action.hyperlink.address=link['url']
            except Exception:
                pass
    cp=prs.core_properties
    cp.title=deck.get('title',''); cp.author=deck.get('metadata',{}).get('author',''); cp.subject=deck.get('purpose',''); cp.comments=deck.get('metadata',{}).get('version','')
    buf=io.BytesIO(); prs.save(buf); data=buf.getvalue()
    data=_patch_theme(data,theme)
    if sections and deck.get('sections'): data=_patch_sections(data,deck)
    if kiosk: data=_patch_kiosk(data)
    core.atomic_write(Path(path),data,overwrite=config.get('_overwrite',False),approved=config.get('_approved',False),retries=config.get('retry',{}).get('attempts',2),backoff_ms=config.get('retry',{}).get('backoff_ms',100))
    return {'path':str(path),'slides':len(slides),'native_charts':native,'kiosk':kiosk}

def _zip_replace(data,replacements):
    src=io.BytesIO(data); out=io.BytesIO()
    with zipfile.ZipFile(src,'r') as zin, zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            content=replacements.get(item.filename,zin.read(item.filename))
            zout.writestr(item,content)
    return out.getvalue()

def _patch_theme(data,theme):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names=[n for n in z.namelist() if n.startswith('ppt/theme/') and n.endswith('.xml')]
            if not names: return data
            xml=z.read(names[0]); root=etree.fromstring(xml)
        cs=root.find('.//a:clrScheme',NS)
        if cs is not None:
            mapping={'dk1':'text','lt1':'background','dk2':'text','lt2':'surface','accent1':'accent','accent2':'accent2','accent3':'success','accent4':'warning','accent5':'danger','accent6':'muted','hlink':'accent','folHlink':'accent2'}
            for child in cs:
                tag=etree.QName(child).localname; role=mapping.get(tag)
                if role and role in theme['colors']:
                    for sub in list(child): child.remove(sub)
                    etree.SubElement(child,'{'+NS['a']+'}srgbClr',{'val':theme['colors'][role].lstrip('#').upper()})
        fs=root.find('.//a:fontScheme',NS)
        if fs is not None:
            for tag,role in (('majorFont','heading'),('minorFont','body')):
                f=fs.find('a:'+tag,NS)
                if f is not None:
                    latin=f.find('a:latin',NS)
                    if latin is not None: latin.set('typeface',theme['fonts'][role])
        return _zip_replace(data,{names[0]:etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)})
    except Exception:
        return data

def _patch_sections(data,deck):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml=z.read('ppt/presentation.xml'); root=etree.fromstring(xml)
            rels=z.read('ppt/_rels/presentation.xml.rels'); rroot=etree.fromstring(rels)
        # map slide order to rIds via sldIdLst
        sldIds=root.find('p:sldIdLst',NS); relmap={r.get('Id'):r.get('Target') for r in rroot}
        order=[]
        for sld in sldIds:
            rid=sld.get('{'+NS['r']+'}id'); target=relmap.get(rid,'')
            m=re.search(r'slide(\d+)\.xml',target); order.append(int(m.group(1))-1 if m else len(order))
        ext=root.find('p:extLst',NS)
        if ext is None: ext=etree.SubElement(root,'{'+NS['p']+'}extLst')
        e=etree.SubElement(ext,'{'+NS['p']+'}ext',{'uri':'{521415D9-36F7-43E2-AB2F-B90AF26B5E84}'})
        sec=etree.SubElement(e,'{'+NS['p14']+'}sectionLst')
        # assign slides to sections by slide definition order
        slide_sections=[s.get('section') for s in deck['slides']]
        for section in deck['sections']:
            sid=section['id']; name=section.get('title',sid)
            guid=str(uuid.uuid5(uuid.NAMESPACE_URL,'universal-presentation-design/section/'+sid)).upper()
            s=etree.SubElement(sec,'{'+NS['p14']+'}section',{'name':name,'id':'{'+guid+'}'})
            lst=etree.SubElement(s,'{'+NS['p14']+'}sldIdLst')
            for idx,ss in enumerate(slide_sections):
                if ss==sid and idx<len(order):
                    # find sldId element for this slide index
                    sldId=sldIds[idx]
                    etree.SubElement(lst,'{'+NS['p14']+'}sldId',{'id':sldId.get('id')})
        return _zip_replace(data,{'ppt/presentation.xml':etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)})
    except Exception:
        return data

def _patch_kiosk(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z: root=etree.fromstring(z.read('ppt/presentation.xml'))
        show=root.find('p:showPr',NS)
        if show is None:
            show=etree.Element('{'+NS['p']+'}showPr')
            idx=next((i for i,c in enumerate(root) if etree.QName(c).localname in {'defaultTextStyle','extLst'}),len(root))
            root.insert(idx,show)
        show.set('loop','1'); show.set('useTimings','1')
        for child in list(show): show.remove(child)
        etree.SubElement(show,'{'+NS['p']+'}kiosk',{'restart':'300000'})
        return _zip_replace(data,{'ppt/presentation.xml':etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)})
    except Exception:
        return data

def read_pptx(path,config):
    try:
        prs=Presentation(str(path))
    except Exception as exc:
        raise core.ToolError('PPTX_READ_FAILED','PowerPoint file could not be opened.',{'path':str(path),'error':str(exc)},core.EXIT_VALIDATION) from exc
    deck={'schema_version':'1.0','title':prs.core_properties.title or path.stem,'audience':'','purpose':prs.core_properties.subject or '','language':'en','direction':'ltr','theme':'neutral','canvas':copy.deepcopy(config['defaults']['canvas']),'sections':[],'metadata':{'source':'pptx','author':prs.core_properties.author or ''},'slides':[],'assets':[]}
    for i,slide in enumerate(prs.slides,1):
        texts=[]; bullets=[]; table=None; chart=None; notes=''
        for shape in slide.shapes:
            if shape.has_text_frame:
                t=shape.text_frame.text.strip()
                if t:
                    if shape==slide.shapes.title or (not texts and len(t)<120): texts.append(t)
                    else: bullets.extend([l.strip() for l in t.splitlines() if l.strip()])
            if shape.has_table:
                tbl=shape.table; headers=[c.text for c in tbl.rows[0].cells]; rows=[[c.text for c in r.cells] for r in list(tbl.rows)[1:]]
                table={'headers':headers,'rows':rows}
            if shape.has_chart:
                ch=shape.chart
                try:
                    cats=[str(c) for c in ch.plots[0].categories]
                    series=[{'name':s.name or f'Series {j+1}','values':[v for v in s.values]} for j,s in enumerate(ch.series)]
                    chart={'type':'column','categories':cats,'series':series,'source':'Imported from PowerPoint'}
                except Exception: pass
        try: notes=slide.notes_slide.notes_text_frame.text
        except Exception: pass
        layout='table' if table else 'chart' if chart else 'title' if i==1 else 'bullets'
        s={'id':f'slide-{i}','layout':layout,'title':texts[0] if texts else f'Slide {i}','bullets':bullets[:12],'notes':notes,'duration_seconds':45}
        if table: s['table']=table
        if chart: s['chart']=chart
        deck['slides'].append(s)
    return core.normalize_deck(deck,config)

def inspect_pptx(path,config):
    prs=Presentation(str(path)); issues=[]; slides=[]
    with zipfile.ZipFile(path) as z:
        bad=z.testzip()
        if bad: issues.append({'severity':'critical','message':'ZIP CRC failure','part':bad})
        media=[n for n in z.namelist() if n.startswith('ppt/media/')]
        ext=[n for n in z.namelist() if n.endswith('.xml')]
    for i,slide in enumerate(prs.slides,1):
        shapes=[]
        for sh in slide.shapes:
            shapes.append({'name':sh.name,'type':str(sh.shape_type),'has_text':sh.has_text_frame,'has_table':sh.has_table,'has_chart':sh.has_chart})
            if sh.has_text_frame and not sh.text_frame.text.strip(): issues.append({'severity':'minor','slide':i,'message':'Empty text shape','shape':sh.name})
        slides.append({'index':i,'shapes':shapes,'notes':bool(slide.notes_slide.notes_text_frame.text.strip()) if slide.has_notes_slide else False})
    return {'path':str(path),'slide_count':len(prs.slides),'width_emu':prs.slide_width,'height_emu':prs.slide_height,'metadata':{'title':prs.core_properties.title,'author':prs.core_properties.author,'subject':prs.core_properties.subject},'media_count':len(media),'xml_parts':len(ext),'slides':slides,'issues':issues}

def recover_pptx(path,config):
    report={'source':str(path),'method':'','recovered_slides':0,'warnings':[]}
    try:
        deck=read_pptx(path,config); report['method']='python-pptx'; report['recovered_slides']=len(deck['slides']); return deck,report
    except Exception as exc:
        report['warnings'].append(f'Normal open failed: {exc}')
    deck={'schema_version':'1.0','title':Path(path).stem+' (recovered)','language':'en','direction':'ltr','theme':'high-contrast','canvas':copy.deepcopy(config['defaults']['canvas']),'sections':[],'metadata':{'source':'recovery'},'slides':[],'assets':[]}
    try:
        with zipfile.ZipFile(path) as z:
            names=sorted([n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$',n)],key=lambda n:int(re.search(r'(\d+)',n).group(1)))
            for n in names:
                try:
                    root=etree.fromstring(z.read(n)); texts=[t.text for t in root.findall('.//a:t',NS) if t.text]
                    deck['slides'].append({'id':Path(n).stem,'layout':'bullets','title':texts[0] if texts else Path(n).stem,'bullets':texts[1:12],'notes':'Recovered from OOXML text; layout and media require review.','duration_seconds':45})
                except Exception as exc:
                    report['warnings'].append(f'{n}: {exc}')
    except Exception as exc:
        raise core.ToolError('RECOVERY_FAILED','File is not a readable OOXML ZIP container.',{'error':str(exc)},core.EXIT_VALIDATION) from exc
    if not deck['slides']:
        deck['slides']=[{'id':'recovered','layout':'bullets','title':'Recovery placeholder','body':'No slide text could be recovered. Restore from backup.','duration_seconds':30}]
    report['method']='ooxml-text-salvage'; report['recovered_slides']=len(deck['slides'])
    return core.normalize_deck(deck,config),report
