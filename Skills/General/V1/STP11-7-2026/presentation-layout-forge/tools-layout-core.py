#!/usr/bin/env python3
"""Shared layout and validation logic for presentation-layout-forge."""
from __future__ import annotations
import json, math, re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE_TYPE, MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_VERTICAL_ANCHOR
from pptx.util import Inches, Pt

EMU=914400
BLACK=RGBColor(0,0,0); WHITE=RGBColor(255,255,255); LIGHT=RGBColor(230,230,230); DARK=RGBColor(33,33,33)

def rgb(hex_color:str)->RGBColor:
    v=hex_color.strip().lstrip('#')
    if len(v)!=6 or not re.fullmatch(r'[0-9a-fA-F]{6}',v):
        raise ValueError(f'invalid color: {hex_color}')
    return RGBColor(int(v[0:2],16),int(v[2:4],16),int(v[4:6],16))

def inches(v): return Inches(float(v))
def rect_of(sh): return (sh.left/EMU, sh.top/EMU, sh.width/EMU, sh.height/EMU)
def area_overlap(a,b):
    ax,ay,aw,ah=a; bx,by,bw,bh=b
    return max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))

def load_json(path:Path)->dict:
    with path.open('r',encoding='utf-8') as f: return json.load(f)
def save_json(path:Path,data:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

def inspect_pptx(path:Path)->dict:
    prs=Presentation(path)
    slides=[]
    for i,sl in enumerate(prs.slides,1):
        shapes=[]
        for sh in sl.shapes:
            text=sh.text if getattr(sh,'has_text_frame',False) else ''
            shapes.append({'name':sh.name,'type':str(sh.shape_type),'x':round(sh.left/EMU,4),'y':round(sh.top/EMU,4),'w':round(sh.width/EMU,4),'h':round(sh.height/EMU,4),'text':text})
        slides.append({'number':i,'layout':sl.slide_layout.name,'shape_count':len(shapes),'shapes':shapes})
    return {'input':str(path),'slide_width_in':prs.slide_width/EMU,'slide_height_in':prs.slide_height/EMU,'slide_count':len(slides),'slides':slides}

def profile_pptx(path:Path)->dict:
    prs=Presentation(path); fonts={}; sizes={}; colors={}; margins=[]; rows=[]
    for sl in prs.slides:
        for sh in sl.shapes:
            if getattr(sh,'has_text_frame',False):
                for p in sh.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.name: fonts[r.font.name]=fonts.get(r.font.name,0)+1
                        if r.font.size: sizes[str(round(r.font.size.pt,1))]=sizes.get(str(round(r.font.size.pt,1)),0)+1
                        try:
                            if r.font.color.rgb: colors[str(r.font.color.rgb)]=colors.get(str(r.font.color.rgb),0)+1
                        except Exception: pass
            x,y,w,h=rect_of(sh)
            if x<2: margins.append(x)
            if sh.shape_type==MSO_SHAPE_TYPE.TABLE:
                rows.append({'rows':len(sh.table.rows),'cols':len(sh.table.columns),'x':x,'y':y,'w':w,'h':h})
    def top(d,n=8): return sorted(d.items(),key=lambda kv:(-kv[1],kv[0]))[:n]
    return {'name':'extracted-profile','slide_width_in':prs.slide_width/EMU,'slide_height_in':prs.slide_height/EMU,'fonts':top(fonts),'font_sizes_pt':top(sizes),'text_colors':top(colors),'common_left_margin_in':round(sorted(margins)[len(margins)//2],2) if margins else 0.4,'tables':rows,'recommended':{'title_x':0.4,'title_y':0.45,'title_w':9.2,'title_h':0.6,'content_x':0.4,'content_y':1.3,'content_w':9.2,'content_h':4.0,'line_width_pt':1.0,'body_font_pt':9,'table_header_fill':'000000','table_header_text':'FFFFFF','table_alt_fill':'D9D9D9'}}

def validate_pptx(path:Path)->dict:
    prs=Presentation(path); issues=[]
    sw,sh=prs.slide_width/EMU,prs.slide_height/EMU
    for si,sl in enumerate(prs.slides,1):
        solids=[x for x in sl.shapes if x.shape_type in (MSO_SHAPE_TYPE.AUTO_SHAPE, MSO_SHAPE_TYPE.TABLE, MSO_SHAPE_TYPE.PICTURE)]
        for shape in sl.shapes:
            x,y,w,h=rect_of(shape)
            if x<-.01 or y<-.01 or x+w>sw+.01 or y+h>sh+.01:
                issues.append({'severity':'error','slide':si,'kind':'out-of-bounds','shape':shape.name,'box':[x,y,w,h]})
            if getattr(shape,'has_text_frame',False) and shape.text.strip():
                fs=[]
                for p in shape.text_frame.paragraphs:
                    for r in p.runs:
                        if r.font.size: fs.append(r.font.size.pt)
                size=max(fs or [12]); lines=max(1,len(shape.text.splitlines())); chars=max((len(z) for z in shape.text.splitlines()),default=0)
                est_lines=max(lines,math.ceil(chars/max(8,w*10*(12/size))))
                if est_lines*size*1.25/72 > h*1.05:
                    issues.append({'severity':'warning','slide':si,'kind':'possible-text-overflow','shape':shape.name})
        for i,a in enumerate(solids):
            for b in solids[i+1:]:
                ov=area_overlap(rect_of(a),rect_of(b))
                if ov>.025:
                    issues.append({'severity':'warning','slide':si,'kind':'shape-overlap','shapes':[a.name,b.name],'area_in2':round(ov,3)})
        # line-through-box check using line bounding box against non-line shapes; endpoints touching edges are allowed
        for ln in [x for x in sl.shapes if x.shape_type==MSO_SHAPE_TYPE.LINE]:
            lx,ly,lw,lh=rect_of(ln); lbox=(min(lx,lx+lw),min(ly,ly+lh),abs(lw),abs(lh))
            for box in solids:
                bx,by,bw,bh=rect_of(box)
                inner=(bx+.04,by+.04,max(0,bw-.08),max(0,bh-.08))
                if area_overlap(lbox,inner)>.003:
                    issues.append({'severity':'warning','slide':si,'kind':'connector-crosses-shape','connector':ln.name,'shape':box.name})
    return {'input':str(path),'valid':not any(i['severity']=='error' for i in issues),'issue_count':len(issues),'issues':issues}

class DeckBuilder:
    def __init__(self,spec:dict,preset:dict,template:Path|None=None):
        self.spec=spec; self.preset=preset
        self.prs=Presentation(template) if template else Presentation()
        self.prs.slide_width=Inches(preset.get('slide_width_in',10)); self.prs.slide_height=Inches(preset.get('slide_height_in',5.625))
        if not template:
            while len(self.prs.slides):
                rid=self.prs.slides._sldIdLst[0].rId; self.prs.part.drop_rel(rid); del self.prs.slides._sldIdLst[0]
        self.style=preset.get('style',{})
    def _slide(self): return self.prs.slides.add_slide(self.prs.slide_layouts[6])
    def _text(self,sl,x,y,w,h,text,size=12,bold=False,align=PP_ALIGN.LEFT,color='000000'):
        sh=sl.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h)); tf=sh.text_frame; tf.clear(); tf.word_wrap=True
        tf.margin_left=tf.margin_right=Inches(.03); tf.margin_top=tf.margin_bottom=Inches(.01)
        p=tf.paragraphs[0]; p.text=text; p.alignment=align
        for r in p.runs: r.font.name=self.style.get('font','Arial'); r.font.size=Pt(size); r.font.bold=bold; r.font.color.rgb=rgb(color)
        return sh
    def _box(self,sl,x,y,w,h,text,size=8,bold=False,fill='FFFFFF',align=PP_ALIGN.CENTER):
        sh=sl.shapes.add_shape(MSO_SHAPE.RECTANGLE,Inches(x),Inches(y),Inches(w),Inches(h)); sh.fill.solid(); sh.fill.fore_color.rgb=rgb(fill); sh.line.color.rgb=rgb('000000'); sh.line.width=Pt(self.style.get('line_width_pt',1))
        tf=sh.text_frame; tf.clear(); tf.vertical_anchor=MSO_VERTICAL_ANCHOR.MIDDLE; tf.margin_left=tf.margin_right=Inches(.04); tf.margin_top=tf.margin_bottom=Inches(.01)
        p=tf.paragraphs[0]; p.text=text; p.alignment=align
        for r in p.runs: r.font.name=self.style.get('font','Arial'); r.font.size=Pt(size); r.font.bold=bold; r.font.color.rgb=BLACK
        return sh
    def _line(self,sl,x1,y1,x2,y2,dash=False,arrow=False):
        c=sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Inches(x1),Inches(y1),Inches(x2),Inches(y2)); c.line.color.rgb=BLACK; c.line.width=Pt(self.style.get('line_width_pt',1))
        if dash:c.line.dash_style=MSO_LINE_DASH_STYLE.DASH
        if arrow:c.line.end_arrowhead=True
        return c
    def _title(self,sl,title): self._text(sl,.4,.38,9.2,.58,title,self.style.get('title_font_pt',24),False)
    def build(self):
        for sd in self.spec['slides']:
            kind=sd['type']; getattr(self,f'build_{kind}')(sd)
        return self.prs
    def build_title(self,sd):
        sl=self._slide(); self._text(sl,.8,2.0,8.4,.55,sd['title'],28,False,PP_ALIGN.CENTER); self._text(sl,1.0,2.65,8,.4,sd.get('subtitle',''),16,False,PP_ALIGN.CENTER,'595959')
    def build_section(self,sd):
        sl=self._slide(); self._text(sl,.8,2.35,8.4,.55,sd['title'],24,False,PP_ALIGN.CENTER)
    def build_bullets(self,sd):
        sl=self._slide(); self._title(sl,sd['title']); box=self._text(sl,.55,1.35,8.9,3.9,'',sd.get('font_pt',13)); tf=box.text_frame; tf.clear()
        for i,item in enumerate(sd['items']):
            p=tf.paragraphs[0] if i==0 else tf.add_paragraph(); p.text='•  '+str(item); p.font.name=self.style.get('font','Arial'); p.font.size=Pt(sd.get('font_pt',13))
    def build_table(self,sd):
        sl=self._slide(); self._title(sl,sd['title']); headers=sd['headers']; rows=sd['rows']; x,y,w,h=.4,1.3,9.2,sd.get('height',3.7)
        shape=sl.shapes.add_table(len(rows)+1,len(headers),Inches(x),Inches(y),Inches(w),Inches(h)); t=shape.table
        widths=sd.get('widths');
        if widths:
            total=sum(widths)
            for i,v in enumerate(widths): t.columns[i].width=Inches(w*v/total)
        for j,v in enumerate(headers): self._cell(t.cell(0,j),v,True,'000000','FFFFFF',sd.get('font_pt',8))
        for i,row in enumerate(rows,1):
            fill='FFFFFF' if i%2 else self.style.get('table_alt_fill','D9D9D9')
            for j,v in enumerate(row): self._cell(t.cell(i,j),v,False,fill,'000000',sd.get('font_pt',8))
        if sd.get('note'): self._text(sl,.4,5.1,9.2,.25,sd['note'],8,False,PP_ALIGN.CENTER)
    def _cell(self,cell,text,bold,fill,color,size):
        cell.fill.solid(); cell.fill.fore_color.rgb=rgb(fill); tf=cell.text_frame; tf.clear(); tf.margin_left=tf.margin_right=Inches(.04); tf.margin_top=tf.margin_bottom=Inches(.02)
        p=tf.paragraphs[0]; p.text=str(text)
        for r in p.runs:r.font.name=self.style.get('font','Arial');r.font.size=Pt(size);r.font.bold=bold;r.font.color.rgb=rgb(color)
    def build_flow(self,sd):
        sl=self._slide(); self._title(sl,sd['title']); steps=sd['steps']; cols=sd.get('columns',3); bw=sd.get('box_w',2.6); bh=sd.get('box_h',1.05); gapx=(9.2-cols*bw)/(cols-1 if cols>1 else 1); x0=.4; y0=1.3; rowgap=.5
        positions=[]
        for i,st in enumerate(steps):
            row=i//cols; col=i%cols
            if row%2==1: col=cols-1-col
            x=x0+col*(bw+gapx); y=y0+row*(bh+rowgap); positions.append((x,y))
            title=st['title']; body=st.get('body',''); self._box(sl,x,y,bw,.32,title,8,True); self._box(sl,x,y+.32,bw,bh-.32,body,7,False,'FFFFFF',PP_ALIGN.LEFT)
        for i in range(len(positions)-1):
            x,y=positions[i]; nx,ny=positions[i+1]
            if abs(y-ny)<.01: self._line(sl,x+bw,y+bh/2,nx,ny+bh/2,False,True)
            else: self._line(sl,x+bw/2,y+bh,nx+bw/2,ny,False,True)
        if sd.get('note'): self._text(sl,.4,5.05,9.2,.25,sd['note'],8,False,PP_ALIGN.CENTER)
    def build_uml(self,sd):
        sl=self._slide(); self._title(sl,sd['title']); nodes=sd['nodes']; by_tier={}
        for n in nodes: by_tier.setdefault(int(n.get('tier',0)),[]).append(n)
        positions={}; top=1.25; avail_h=3.85; tiers=sorted(by_tier); rowstep=avail_h/max(1,len(tiers))
        for ri,tier in enumerate(tiers):
            row=by_tier[tier]; gap=.35; total=sum(float(n.get('w',2.0)) for n in row)+gap*(len(row)-1); x=(10-total)/2; y=top+ri*rowstep
            for n in row:
                w=float(n.get('w',2.0)); hh=.34 if not n.get('stereotype') else .42; bh=float(n.get('h',.95))-hh
                label=n['label']+(('\n'+n['stereotype']) if n.get('stereotype') else '')
                self._box(sl,x,y,w,hh,label,7.8,True); self._box(sl,x,y+hh,w,bh,'\n'.join(n.get('body',[])),6.8,False,'FFFFFF',PP_ALIGN.LEFT); positions[n['id']]=(x,y,w,hh+bh); x+=w+gap
        rels=sd.get('relationships',[])
        implements=[r for r in rels if r.get('kind')=='implements']
        other=[r for r in rels if r.get('kind')!='implements']
        if implements:
            srcs=[positions[r['from']] for r in implements]; target=positions[implements[0]['to']]; bus_y=min(s[1] for s in srcs)-.18
            left=min(x+w/2 for x,y,w,h in srcs); right=max(x+w/2 for x,y,w,h in srcs)
            self._line(sl,left,bus_y,right,bus_y,True,False)
            for x,y,w,h in srcs:self._line(sl,x+w/2,y,x+w/2,bus_y,True,False)
            tx,ty,tw,th=target; self._line(sl,tx+tw/2,bus_y,tx+tw/2,ty+th,True,True); self._text(sl,tx+tw/2+.08,bus_y-.25,.8,.18,'implements',6.5)
        for r in other:
            a=positions[r['from']]; b=positions[r['to']]; ax,ay,aw,ah=a; bx,by,bw,bh=b
            label=r.get('label','')
            # horizontal if nearly same row, otherwise orthogonal through a clear midline
            if abs(ay-by)<.25:
                self._line(sl,ax+aw,ay+ah/2,bx,by+bh/2,False,True); lx=(ax+aw+bx)/2-.35; ly=ay+ah/2-.25
            else:
                sx=ax+aw/2; sy=ay+ah; tx=bx+bw/2; ty=by; mid=(sy+ty)/2
                self._line(sl,sx,sy,sx,mid,False,False); self._line(sl,sx,mid,tx,mid,False,False); self._line(sl,tx,mid,tx,ty,False,True); lx=(sx+tx)/2-.35; ly=mid-.24
            if label:self._text(sl,lx,ly,.7,.18,label,6.5,False,PP_ALIGN.CENTER)
        if sd.get('note'): self._text(sl,.45,5.20,9.1,.22,sd['note'],8,False,PP_ALIGN.CENTER)
