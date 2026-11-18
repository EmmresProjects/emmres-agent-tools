"""Deterministic layout, SVG, and self-contained HTML rendering.

Loaded by tools-presentation-design.py after tools-core.py. No network or shell calls.
"""
from __future__ import annotations
import base64, copy, math, re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr
import tools_core as core

try:
    from PIL import ImageFont
except ImportError:
    ImageFont = None

@lru_cache(maxsize=128)
def _font(size: int, bold: bool = False):
    if ImageFont is None:
        return None
    try:
        return ImageFont.load_default(size=max(6, int(size)))
    except TypeError:
        return ImageFont.load_default()

def text_width(text: str, size: float, bold: bool = False) -> float:
    f = _font(int(size), bold)
    if f is None:
        return len(text) * size * 0.55
    try:
        return f.getlength(text)
    except Exception:
        return len(text) * size * 0.55

def wrap_text(text: str, width: float, size: float, max_lines: int = 12, bold: bool = False) -> list[str]:
    text = str(text or "")
    if not text:
        return [""]
    lines = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for word in words:
            trial = (line + " " + word).strip()
            if text_width(trial, size, bold) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
            if len(lines) >= max_lines:
                break
        if line and len(lines) < max_lines:
            lines.append(line)
        if len(lines) >= max_lines:
            break
    if len(lines) == max_lines and text_width(" ".join(lines), size, bold) < text_width(text, size, bold) * 0.98:
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    return lines or [""]

def fit_text(text: str, width: float, height: float, size: float, min_size: float = 12, bold: bool = False, line_height: float = 1.22) -> tuple[list[str], float]:
    s = size
    while s >= min_size:
        lines = wrap_text(text, width, s, max_lines=max(1, int(height / (s * line_height))), bold=bold)
        if len(lines) * s * line_height <= height and all(text_width(l, s, bold) <= width * 1.02 for l in lines):
            return lines, s
        s -= 1
    return wrap_text(text, width, min_size, max_lines=max(1, int(height / (min_size * line_height))), bold=bold), min_size

def P(kind, **kw):
    d = {"type": kind}; d.update(kw); return d

def _colors(theme):
    return theme["colors"]

def _scale(theme, key, default):
    return float(theme.get("type_scale", {}).get(key, default))

def layout_slide(deck, slide, index, theme, variant="standard"):
    canvas = deck.get("canvas", {"width":1280,"height":720,"margin":64,"grid":12,"safe":48})
    W,H,M = float(canvas["width"]), float(canvas["height"]), float(canvas.get("margin",64))
    c = _colors(theme)
    prims = [P("rect", x=0,y=0,w=W,h=H,fill=c["background"],role="background")]
    title_size = _scale(theme,"title",44)
    body_size = _scale(theme,"body",22)
    content_y = M + title_size + 28
    content_h = H - content_y - M
    title_w = W - 2*M
    if variant in {"minimal","focal"}:
        M2 = M*1.4; title_w = W-2*M2; content_y = M2 + title_size + 36; content_h = H-content_y-M2
    if variant == "dense":
        body_size *= 0.9; title_size *= 0.9
    if variant in {"poster","magazine","comic"}:
        title_size *= 1.15
    layout = slide.get("layout","bullets")
    if layout in {"title","closing","section"}:
        ty = H*0.30 if layout != "section" else H*0.38
        lines, ts = fit_text(slide.get("title",""), title_w, H*0.32, title_size*1.15, 24, True)
        prims.append(P("text", x=M, y=ty, w=title_w, h=ts*1.2*len(lines), text=slide.get("title",""), size=ts, color=c["text"], font=theme["fonts"]["heading"], bold=True, align="left", role="title", lines=lines))
        y = ty + ts*1.2*len(lines) + 18
        if slide.get("subtitle"):
            sl, ss = fit_text(slide["subtitle"], title_w, H*0.22, _scale(theme,"subtitle",26), 16)
            prims.append(P("text", x=M, y=y, w=title_w, h=ss*1.25*len(sl), text=slide["subtitle"], size=ss, color=c["muted"], font=theme["fonts"]["body"], lines=sl, role="subtitle"))
            y += ss*1.25*len(sl)+16
        for b in (slide.get("bullets") or [])[:6]:
            bl, bs = fit_text("• "+b, title_w, body_size*2.2, body_size, 14)
            prims.append(P("text", x=M, y=y, w=title_w, h=bs*1.25*len(bl), text="• "+b, size=bs, color=c["text"], font=theme["fonts"]["body"], lines=bl))
            y += bs*1.3*len(bl)+6
    else:
        lines, ts = fit_text(slide.get("title",""), title_w, title_size*1.6, title_size, 20, True)
        prims.append(P("text", x=M, y=M*0.65, w=title_w, h=ts*1.2*len(lines), text=slide.get("title",""), size=ts, color=c["text"], font=theme["fonts"]["heading"], bold=True, lines=lines, role="title"))
        if slide.get("subtitle"):
            prims.append(P("text", x=M, y=M*0.65+ts*1.2*len(lines)+4, w=title_w, h=30, text=slide["subtitle"], size=_scale(theme,"caption",16), color=c["muted"], font=theme["fonts"]["body"], lines=wrap_text(slide["subtitle"], title_w, _scale(theme,"caption",16), 2)))
        box = {"x":M,"y":content_y,"w":W-2*M,"h":content_h}
        if variant in {"thirds","golden","asymmetric"} and layout in {"bullets","image","quote"}:
            split = 0.62 if variant != "golden" else 0.618
            left = {"x":M,"y":content_y,"w":(W-2*M)*split-12,"h":content_h}
            right = {"x":M+(W-2*M)*split+12,"y":content_y,"w":(W-2*M)*(1-split)-12,"h":content_h}
            _text_block(prims, slide, left, theme, body_size)
            if slide.get("image"):
                prims.append(_image_prim(slide["image"], right, c))
            else:
                prims.append(P("rect", x=right["x"],y=right["y"],w=right["w"],h=right["h"],fill=c["surface"],radius=theme.get("radius",12)))
                prims.append(P("text", x=right["x"]+16,y=right["y"]+16,w=right["w"]-32,h=right["h"]-32,text=slide.get("notes","") or slide.get("alt_text","") or "Supporting visual",size=body_size*0.85,color=c["muted"],font=theme["fonts"]["body"],lines=wrap_text(slide.get("notes","") or slide.get("alt_text","") or "Supporting visual", right["w"]-32, body_size*0.85, 10)))
        elif layout == "chart" and slide.get("chart"):
            prims.extend(chart_primitives(slide["chart"], box, theme))
        elif layout == "table" and slide.get("table"):
            prims.extend(table_primitives(slide["table"], box, theme))
        elif layout == "diagram" and slide.get("diagram"):
            prims.extend(diagram_primitives(slide["diagram"], box, theme))
        elif layout == "timeline" and slide.get("timeline"):
            prims.extend(timeline_primitives(slide["timeline"], box, theme))
        elif layout == "image" and slide.get("image"):
            prims.append(_image_prim(slide["image"], box, c))
            if slide["image"].get("credit"):
                prims.append(P("text", x=M,y=H-M+8,w=W-2*M,h=24,text=slide["image"]["credit"],size=14,color=c["muted"],font=theme["fonts"]["body"],lines=[slide["image"]["credit"]]))
        elif layout == "kpi" and slide.get("kpis"):
            prims.extend(kpi_primitives(slide["kpis"], box, theme))
        elif layout == "quiz" and slide.get("quiz"):
            prims.extend(quiz_primitives(slide["quiz"], box, theme, slide.get("id")))
        elif layout == "quote":
            q = slide.get("body") or slide.get("subtitle") or slide.get("title","")
            lines, qs = fit_text("“"+q+"”", box["w"], box["h"]*0.7, body_size*1.6, 20)
            prims.append(P("text", x=box["x"],y=box["y"],w=box["w"],h=qs*1.3*len(lines),text=q,size=qs,color=c["text"],font=theme["fonts"]["heading"],lines=lines,italic=True))
        elif layout == "comparison" and slide.get("table"):
            prims.extend(table_primitives(slide["table"], box, theme))
        elif layout == "dashboard":
            if slide.get("kpis"): prims.extend(kpi_primitives(slide["kpis"], {"x":box["x"],"y":box["y"],"w":box["w"],"h":box["h"]*0.32}, theme))
            if slide.get("chart"): prims.extend(chart_primitives(slide["chart"], {"x":box["x"],"y":box["y"]+box["h"]*0.36,"w":box["w"],"h":box["h"]*0.64}, theme))
        else:
            _text_block(prims, slide, box, theme, body_size)
    for obj in sorted(slide.get("objects") or [], key=lambda o:o.get("z",0)):
        prims.append(_object_prim(obj, c, theme))
    prims.append(P("text", x=W-M-80,y=H-M+18,w=80,h=24,text=str(index+1),size=14,color=c["muted"],font=theme["fonts"]["body"],align="right",lines=[str(index+1)],role="page"))
    if slide.get("link"):
        label = slide["link"].get("label") or "Continue"
        prims.append(P("rect", x=M,y=H-M+6,w=180,h=34,fill=c["accent"],radius=8,link=slide["link"]))
        prims.append(P("text", x=M+12,y=H-M+12,w=156,h=24,text=label,size=15,color="#FFFFFF",font=theme["fonts"]["body"],lines=[label],link=slide["link"]))
    return {"primitives":prims,"canvas":canvas,"box":{"x":M,"y":content_y,"w":W-2*M,"h":content_h}}

def _text_block(prims, slide, box, theme, body_size):
    c=_colors(theme); y=box["y"]
    if slide.get("body"):
        lines, bs = fit_text(slide["body"], box["w"], box["h"]*0.45, body_size, 14)
        prims.append(P("text", x=box["x"],y=y,w=box["w"],h=bs*1.25*len(lines),text=slide["body"],size=bs,color=c["text"],font=theme["fonts"]["body"],lines=lines))
        y += bs*1.3*len(lines)+14
    bullets = slide.get("bullets") or []
    if bullets:
        avail = box["y"]+box["h"]-y
        per = max(body_size*1.4, avail/max(1,len(bullets)))
        for b in bullets[:12]:
            lines, bs = fit_text("• "+str(b), box["w"]-12, per, body_size, 13)
            prims.append(P("text", x=box["x"],y=y,w=box["w"],h=bs*1.25*len(lines),text="• "+str(b),size=bs,color=c["text"],font=theme["fonts"]["body"],lines=lines))
            y += bs*1.28*len(lines)+8
            if y > box["y"]+box["h"]: break

def _image_prim(img, box, c):
    return P("image", x=box["x"],y=box["y"],w=box["w"],h=box["h"],path=img.get("path",""),alt=img.get("alt",""),fit=img.get("fit","cover"),focal_x=img.get("focal_x",0.5),focal_y=img.get("focal_y",0.5),fill=c["surface"])

def _object_prim(obj,c,theme):
    t=obj.get("type","rect")
    if t=="text":
        size=obj.get("size",_scale(theme,"body",22)); lines=wrap_text(obj.get("text",""),obj.get("w",200),size,8)
        return P("text",x=obj.get("x",0),y=obj.get("y",0),w=obj.get("w",200),h=obj.get("h",60),text=obj.get("text",""),size=size,color=obj.get("fill",c["text"]),font=theme["fonts"]["body"],lines=lines,id=obj.get("id"))
    if t=="line":
        return P("line",x1=obj.get("x",0),y1=obj.get("y",0),x2=obj.get("x",0)+obj.get("w",100),y2=obj.get("y",0)+obj.get("h",0),stroke=obj.get("stroke",c["accent"]),width=obj.get("size",3),id=obj.get("id"))
    if t=="image":
        return _image_prim({"path":obj.get("path",""),"alt":obj.get("text",""),"fit":"contain"}, {"x":obj.get("x",0),"y":obj.get("y",0),"w":obj.get("w",200),"h":obj.get("h",150)}, c)
    return P("ellipse" if t=="ellipse" else "rect",x=obj.get("x",0),y=obj.get("y",0),w=obj.get("w",100),h=obj.get("h",80),fill=obj.get("fill",c["accent"]),stroke=obj.get("stroke"),radius=theme.get("radius",12),id=obj.get("id"))

def _num(v, default=0.0):
    try:
        if v is None: return default
        return float(v)
    except (TypeError, ValueError):
        return default

def chart_primitives(chart, box, theme):
    c=_colors(theme); prims=[]; kind=chart.get("type","column")
    series=chart.get("series") or []; cats=[str(x) for x in chart.get("categories") or []]
    if not series:
        return [P("text",x=box["x"],y=box["y"],w=box["w"],h=40,text="No chart data",size=20,color=c["muted"],font=theme["fonts"]["body"],lines=["No chart data"])]
    if kind=="kpi":
        kpis=[{"label":s.get("name",f"Metric {i+1}"),"value":(s.get("values") or [0])[-1]} for i,s in enumerate(series)]
        return kpi_primitives(kpis,box,theme)
    if kind=="funnel":
        vals=[_num(v) for v in (series[0].get("values") or [])]; mx=max(vals+[1]); n=max(1,len(vals))
        for i,v in enumerate(vals):
            w=box["w"]*0.85*(v/mx if mx else 0); x=box["x"]+(box["w"]-w)/2; y=box["y"]+i*(box["h"]/n); h=box["h"]/n-8
            prims.append(P("rect",x=x,y=y,w=max(w,4),h=h,fill=c["accent"] if i%2==0 else c["accent2"],radius=6))
            label=(cats[i] if i<len(cats) else f"Stage {i+1}")+f": {v:g}"
            prims.append(P("text",x=box["x"],y=y+h/2-12,w=box["w"],h=24,text=label,size=16,color=c["text"],font=theme["fonts"]["body"],align="center",lines=[label]))
        return prims
    if kind=="heatmap":
        n=len(cats) or max(len(s.get("values") or []) for s in series); m=len(series); vals=[_num(v) for s in series for v in s.get("values") or []]; lo,hi=min(vals+[0]),max(vals+[1])
        cw=box["w"]/max(1,n); ch=(box["h"]-30)/max(1,m)
        for r,s in enumerate(series):
            prims.append(P("text",x=box["x"],y=box["y"]+r*ch+ch/2-10,w=110,h=20,text=s.get("name",f"Row {r+1}"),size=13,color=c["text"],font=theme["fonts"]["body"],lines=[s.get("name",f"Row {r+1}")]))
            for i in range(n):
                v=_num((s.get("values") or [0]*n)[i] if i<len(s.get("values") or []) else 0); t=(v-lo)/(hi-lo) if hi>lo else 0
                prims.append(P("rect",x=box["x"]+120+i*cw,y=box["y"]+r*ch,w=cw-4,h=ch-4,fill=core.mix(c["surface"],c["accent"],t),radius=3))
                prims.append(P("text",x=box["x"]+120+i*cw,y=box["y"]+r*ch+ch/2-10,w=cw-4,h=20,text=f"{v:g}",size=12,color=c["text"],font=theme["fonts"]["body"],align="center",lines=[f"{v:g}"]))
        return prims
    if kind=="map":
        prims.append(P("rect",x=box["x"],y=box["y"],w=box["w"],h=box["h"],fill=c["surface"],radius=8))
        pts=chart.get("points") or []
        for i,p in enumerate(pts):
            x=box["x"]+_num(p.get("x",0.5))*box["w"]; y=box["y"]+_num(p.get("y",0.5))*box["h"]; r=8+_num(p.get("value",1))*4
            prims.append(P("ellipse",x=x-r,y=y-r,w=2*r,h=2*r,fill=c["accent"]))
            prims.append(P("text",x=x+10,y=y-10,w=160,h=20,text=str(p.get("label",f"Point {i+1}")),size=13,color=c["text"],font=theme["fonts"]["body"],lines=[str(p.get("label",f"Point {i+1}"))]))
        prims.append(P("text",x=box["x"],y=box["y"]+box["h"]-22,w=box["w"],h=20,text="Schematic coordinate map; not a geographic basemap.",size=12,color=c["muted"],font=theme["fonts"]["body"],lines=["Schematic coordinate map; not a geographic basemap."]))
        return prims
    if kind=="small-multiples":
        n=len(series); cols=math.ceil(math.sqrt(n)); rows=math.ceil(n/cols)
        for i,s in enumerate(series):
            bx={"x":box["x"]+(i%cols)*(box["w"]/cols),"y":box["y"]+(i//cols)*(box["h"]/rows),"w":box["w"]/cols-12,"h":box["h"]/rows-12}
            prims.extend(chart_primitives({"type":"line","categories":cats,"series":[s],"source":chart.get("source")},bx,theme))
        return prims
    # Cartesian charts
    left=box["x"]+64; top=box["y"]+18; right=box["x"]+box["w"]-20; bottom=box["y"]+box["h"]-46
    pw=right-left; ph=bottom-top
    vals=[_num(v) for s in series for v in s.get("values") or [] if v is not None]
    if not vals: vals=[0]
    lo=min(vals+[0] if chart.get("zero_baseline",True) and kind in {"bar","column","area"} else vals)
    hi=max(vals+[0] if chart.get("zero_baseline",True) and kind in {"bar","column","area"} else vals)
    if hi==lo: hi=lo+1
    pad=(hi-lo)*0.08; lo-=pad; hi+=pad
    def Y(v): return bottom-(_num(v)-lo)/(hi-lo)*ph
    def X(i,n): return left+(i+0.5)*pw/max(1,n)
    prims.append(P("line",x1=left,y1=bottom,x2=right,y2=bottom,stroke=c["grid"],width=1.5))
    prims.append(P("line",x1=left,y1=top,x2=left,y2=bottom,stroke=c["grid"],width=1.5))
    for g in range(5):
        v=lo+(hi-lo)*g/4; y=Y(v)
        prims.append(P("line",x1=left,y1=y,x2=right,y2=y,stroke=c["grid"],width=0.6))
        prims.append(P("text",x=box["x"],y=y-9,w=58,h=18,text=f"{v:.3g}",size=12,color=c["muted"],font=theme["fonts"]["body"],align="right",lines=[f"{v:.3g}"]))
    palette=[c["accent"],c["accent2"],c["success"],c["warning"],c["danger"]]
    n=max(len(cats), max((len(s.get("values") or []) for s in series), default=1))
    if kind in {"bar","column","waterfall"}:
        group=pw/max(1,n); bw=group*0.7/max(1,len(series))
        cumulative=0.0
        for si,s in enumerate(series):
            for i,v in enumerate(s.get("values") or []):
                v=_num(v)
                if kind=="waterfall":
                    start=cumulative; cumulative+=v; y0=Y(start); y1=Y(cumulative); y=min(y0,y1); h=max(2,abs(y1-y0)); x=left+i*group+group*0.15; w=group*0.7
                    fill=c["success"] if v>=0 else c["danger"]
                else:
                    zero=Y(0); yv=Y(v); y=min(zero,yv); h=max(2,abs(yv-zero)); x=left+i*group+group*0.15+si*bw; w=bw*0.9
                    fill=palette[si%len(palette)]
                if kind=="bar":
                    # horizontal bars: reinterpret x/y
                    y= top+i*(ph/max(1,n))+si*(ph/max(1,n)/max(1,len(series)))+4; h=(ph/max(1,n))/max(1,len(series))*0.8; x=left; w=max(2,(v-lo)/(hi-lo)*pw)
                prims.append(P("rect",x=x,y=y,w=w,h=h,fill=fill,radius=3))
        for i,cat in enumerate(cats[:n]):
            x=left+i*(pw/max(1,n)) if kind!="bar" else box["x"]
            y=bottom+8 if kind!="bar" else top+i*(ph/max(1,n))+4
            prims.append(P("text",x=x,y=y,w=(pw/max(1,n)) if kind!="bar" else 58,h=30,text=cat,size=12,color=c["muted"],font=theme["fonts"]["body"],align="center" if kind!="bar" else "right",lines=wrap_text(cat,60,12,2)))
    else:
        for si,s in enumerate(series):
            values=s.get("values") or []; color=palette[si%len(palette)]
            pts=[(X(i,n),Y(v)) for i,v in enumerate(values) if v is not None]
            if kind=="area" and pts:
                # approximate area with vertical slices
                for i in range(len(pts)-1):
                    x0,y0=pts[i]; x1,y1=pts[i+1]
                    prims.append(P("rect",x=x0,y=min(y0,y1),w=max(1,x1-x0),h=bottom-min(y0,y1),fill=color,opacity=0.25))
            for i in range(len(pts)-1):
                prims.append(P("line",x1=pts[i][0],y1=pts[i][1],x2=pts[i+1][0],y2=pts[i+1][1],stroke=color,width=2.5))
            for i,(x,y) in enumerate(pts):
                r=4 if kind!="bubble" else 6+_num((s.get("sizes") or [1]*len(pts))[i] if i<len(s.get("sizes") or []) else 1)*2
                prims.append(P("ellipse",x=x-r,y=y-r,w=2*r,h=2*r,fill=color))
                if kind=="uncertainty":
                    err=_num((s.get("errors") or [0]*len(pts))[i] if i<len(s.get("errors") or []) else 0)
                    prims.append(P("line",x1=x,y1=Y(_num(values[i])-err),x2=x,y2=Y(_num(values[i])+err),stroke=color,width=1.5))
            if s.get("name"):
                prims.append(P("text",x=right-180,y=top+si*20,w=180,h=18,text=s["name"],size=13,color=color,font=theme["fonts"]["body"],lines=[s["name"]]))
        for i,cat in enumerate(cats[:n]):
            prims.append(P("text",x=X(i,n)-30,y=bottom+8,w=60,h=30,text=cat,size=12,color=c["muted"],font=theme["fonts"]["body"],align="center",lines=wrap_text(cat,60,12,2)))
    for ann in chart.get("annotations") or []:
        i=int(ann.get("index",0)); x=X(i,n); y=top+10
        prims.append(P("text",x=min(x,right-180),y=y,w=180,h=36,text=ann.get("text",""),size=13,color=c["warning"],font=theme["fonts"]["body"],lines=wrap_text(ann.get("text",""),180,13,2)))
    if chart.get("source"):
        prims.append(P("text",x=box["x"],y=box["y"]+box["h"]-18,w=box["w"],h=18,text="Source: "+chart["source"],size=12,color=c["muted"],font=theme["fonts"]["body"],lines=wrap_text("Source: "+chart["source"],box["w"],12,1)))
    return prims

def table_primitives(table, box, theme):
    c=_colors(theme); headers=[str(h) for h in table.get("headers") or []]; rows=table.get("rows") or []
    if not headers and rows: headers=[f"Column {i+1}" for i in range(len(rows[0]))]
    cols=max(1,len(headers)); row_h=min(44, box["h"]/max(1,len(rows)+1)); prims=[]
    widths=[box["w"]/cols]*cols
    x=box["x"]
    for j,h in enumerate(headers):
        prims.append(P("rect",x=x,y=box["y"],w=widths[j],h=row_h,fill=c["accent"]))
        prims.append(P("text",x=x+8,y=box["y"]+row_h/2-10,w=widths[j]-16,h=20,text=h,size=15,color="#FFFFFF",font=theme["fonts"]["body"],bold=True,lines=wrap_text(h,widths[j]-16,15,1)))
        x+=widths[j]
    for i,row in enumerate(rows[:max(1,int((box["h"]-row_h)/row_h))]):
        y=box["y"]+row_h*(i+1); x=box["x"]
        for j in range(cols):
            val=str(row[j]) if j<len(row) else ""
            prims.append(P("rect",x=x,y=y,w=widths[j],h=row_h,fill=c["surface"] if i%2==0 else c["background"],stroke=c["grid"]))
            prims.append(P("text",x=x+8,y=y+row_h/2-10,w=widths[j]-16,h=20,text=val,size=14,color=c["text"],font=theme["fonts"]["body"],lines=wrap_text(val,widths[j]-16,14,1)))
            x+=widths[j]
    if table.get("caption"):
        prims.append(P("text",x=box["x"],y=box["y"]+box["h"]-18,w=box["w"],h=18,text=table["caption"],size=12,color=c["muted"],font=theme["fonts"]["body"],lines=wrap_text(table["caption"],box["w"],12,1)))
    return prims

def _node_positions(diagram, box):
    nodes=diagram.get("nodes") or []; kind=diagram.get("type","flow"); n=len(nodes); pos={}
    if not n: return pos
    if kind in {"cycle","causal","mindmap","concept","network"}:
        cx=box["x"]+box["w"]/2; cy=box["y"]+box["h"]/2; r=min(box["w"],box["h"])*0.34
        for i,node in enumerate(nodes):
            if kind in {"mindmap","concept"} and i==0: pos[node["id"]]=(cx,cy); continue
            angle=2*math.pi*i/max(1,n if kind not in {"mindmap","concept"} else n-1)
            pos[node["id"]]=(cx+r*math.cos(angle),cy+r*math.sin(angle))
    elif kind=="pyramid":
        for i,node in enumerate(nodes):
            w=box["w"]*(0.35+0.65*(i+1)/n); pos[node["id"]]=(box["x"]+box["w"]/2, box["y"]+box["h"]*(i+0.5)/n)
    elif kind in {"swimlane","journey","blueprint"}:
        lanes=[]
        for node in nodes:
            lane=node.get("lane","Lane 1")
            if lane not in lanes: lanes.append(lane)
        counts={l:0 for l in lanes}
        for node in nodes:
            lane=node.get("lane","Lane 1"); idx=counts[lane]; counts[lane]+=1
            pos[node["id"]]=(box["x"]+box["w"]*(idx+0.5)/max(1,max(counts.values())), box["y"]+box["h"]*(lanes.index(lane)+0.5)/len(lanes))
    elif kind=="hierarchy":
        edges=diagram.get("edges") or []; children={}
        for e in edges: children.setdefault(e["from"],[]).append(e["to"])
        roots=[node["id"] for node in nodes if not any(e["to"]==node["id"] for e in edges)] or [nodes[0]["id"]]
        levels={}; queue=[(r,0) for r in roots]
        while queue:
            nid,lvl=queue.pop(0)
            if nid in levels: continue
            levels[nid]=lvl
            for ch in children.get(nid,[]): queue.append((ch,lvl+1))
        maxlvl=max(levels.values() or [0]); bylevel={}
        for node in nodes: bylevel.setdefault(levels.get(node["id"],0),[]).append(node["id"])
        for lvl,ids in bylevel.items():
            for i,nid in enumerate(ids): pos[nid]=(box["x"]+box["w"]*(i+0.5)/len(ids), box["y"]+box["h"]*(lvl+0.5)/(maxlvl+1))
    else:
        cols=n if kind in {'flow','process'} and n<=4 else min(4,max(1,math.ceil(math.sqrt(n))))
        for i,node in enumerate(nodes):
            if node.get("x") is not None and node.get("y") is not None:
                pos[node["id"]]=(box["x"]+_num(node["x"])*box["w"],box["y"]+_num(node["y"])*box["h"])
            else:
                rows=math.ceil(n/cols); pos[node["id"]]=(box["x"]+box["w"]*((i%cols)+0.5)/cols,box["y"]+box["h"]*((i//cols)+0.5)/rows)
    return pos

def diagram_primitives(diagram, box, theme):
    c=_colors(theme); nodes=diagram.get("nodes") or []; edges=diagram.get("edges") or []; pos=_node_positions(diagram,box); prims=[]
    kind=diagram.get("type","flow")
    if kind in {"swimlane","journey","blueprint"}:
        lanes=[]
        for node in nodes:
            lane=node.get("lane","Lane 1")
            if lane not in lanes: lanes.append(lane)
        for i,lane in enumerate(lanes):
            y=box["y"]+box["h"]*i/len(lanes)
            prims.append(P("rect",x=box["x"],y=y,w=box["w"],h=box["h"]/len(lanes),fill=c["surface"] if i%2==0 else c["background"],stroke=c["grid"]))
            prims.append(P("text",x=box["x"]+8,y=y+8,w=120,h=20,text=lane,size=13,color=c["muted"],font=theme["fonts"]["body"],lines=[lane]))
    if kind=="venn":
        for i,node in enumerate(nodes[:3]):
            r=min(box["w"],box["h"])*0.28; cx=box["x"]+box["w"]*(0.35+0.15*i); cy=box["y"]+box["h"]*(0.5 if i<2 else 0.65)
            prims.append(P("ellipse",x=cx-r,y=cy-r,w=2*r,h=2*r,fill=[c["accent"],c["accent2"],c["success"]][i],opacity=0.35,stroke=c["text"]))
            prims.append(P("text",x=cx-r,y=cy-10,w=2*r,h=20,text=node.get("label",""),size=15,color=c["text"],font=theme["fonts"]["body"],align="center",lines=[node.get("label","")]))
        return prims
    nw=min(180,box["w"]/max(1,min(4,len(nodes)))) ; nh=64
    for e in edges:
        if e.get("from") in pos and e.get("to") in pos:
            x1,y1=pos[e["from"]]; x2,y2=pos[e["to"]]
            prims.append(P("line",x1=x1,y1=y1,x2=x2,y2=y2,stroke=c["muted"],width=2,arrow=True))
            if e.get("label"):
                prims.append(P("text",x=(x1+x2)/2-60,y=(y1+y2)/2-22,w=120,h=20,text=e["label"],size=12,color=c["muted"],font=theme["fonts"]["body"],align="center",lines=wrap_text(e["label"],120,12,1)))
    for i,node in enumerate(nodes):
        x,y=pos.get(node["id"],(box["x"],box["y"]))
        if kind=="pyramid":
            w=box["w"]*(0.35+0.65*(i+1)/len(nodes)); h=box["h"]/len(nodes)*0.8
            prims.append(P("rect",x=x-w/2,y=y-h/2,w=w,h=h,fill=core.mix(c["accent"],c["accent2"],i/max(1,len(nodes)-1)),radius=6))
        else:
            w=nw; h=nh
            prims.append(P("rect",x=x-w/2,y=y-h/2,w=w,h=h,fill=c["accent"] if kind in {"flow","process"} else c["surface"],stroke=c["accent"],radius=theme.get("radius",12)))
        lines=wrap_text(node.get("label",""),w-16,15,3)
        prims.append(P("text",x=x-w/2+8,y=y-10*len(lines),w=w-16,h=20*len(lines),text=node.get("label",""),size=15,color="#FFFFFF" if kind in {"flow","process"} else c["text"],font=theme["fonts"]["body"],align="center",lines=lines))
    return prims

def _parse_date(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00")).date()
    except ValueError:
        try: return datetime.strptime(str(s),"%Y-%m-%d").date()
        except ValueError: return None

def timeline_primitives(timeline, box, theme):
    c=_colors(theme); events=timeline.get("events") or []; mode=timeline.get("mode","timeline"); prims=[]
    if not events: return prims
    status_color={"complete":c["success"],"active":c["accent"],"planned":c["muted"],"blocked":c["danger"],"done":c["success"],"in-progress":c["accent"]}
    if mode in {"gantt","roadmap"}:
        dates=[_parse_date(e.get("start") or e.get("date")) for e in events]+[_parse_date(e.get("end")) for e in events]
        dates=[d for d in dates if d]; lo=min(dates) if dates else date.today(); hi=max(dates) if dates else lo
        span=max(1,(hi-lo).days); lanes=[]
        for e in events:
            lane=e.get("lane") or e.get("status","planned")
            if lane not in lanes: lanes.append(lane)
        rh=box["h"]/max(1,len(lanes))
        for i,lane in enumerate(lanes):
            prims.append(P("text",x=box["x"],y=box["y"]+i*rh+rh/2-10,w=120,h=20,text=str(lane),size=13,color=c["muted"],font=theme["fonts"]["body"],lines=wrap_text(str(lane),120,13,1)))
        for e in events:
            lane=e.get("lane") or e.get("status","planned"); i=lanes.index(lane); s=_parse_date(e.get("start") or e.get("date")) or lo; en=_parse_date(e.get("end")) or s
            x=box["x"]+140+(s-lo).days/span*(box["w"]-160); w=max(8,(en-s).days/span*(box["w"]-160)); y=box["y"]+i*rh+rh*0.25; h=rh*0.5
            prims.append(P("rect",x=x,y=y,w=w,h=h,fill=status_color.get(e.get("status","planned"),c["accent"]),radius=6))
            prims.append(P("text",x=x+6,y=y+h/2-9,w=max(w-12,80),h=18,text=e.get("label",""),size=12,color="#FFFFFF",font=theme["fonts"]["body"],lines=wrap_text(e.get("label",""),max(w-12,80),12,1)))
        return prims
    if mode in {"now-next-later","horizon"}:
        cols=["Now","Next","Later"] if mode=="now-next-later" else ["Horizon 1","Horizon 2","Horizon 3"]
        for i,col in enumerate(cols):
            x=box["x"]+i*box["w"]/3; prims.append(P("rect",x=x,y=box["y"],w=box["w"]/3-12,h=box["h"],fill=c["surface"],radius=8))
            prims.append(P("text",x=x+12,y=box["y"]+12,w=box["w"]/3-36,h=24,text=col,size=18,color=c["text"],font=theme["fonts"]["heading"],bold=True,lines=[col]))
            items=[e for j,e in enumerate(events) if (e.get("lane") or e.get("status") or cols[j%3])==col or (mode=="now-next-later" and e.get("status")==["active","planned","planned"][i]) or (mode!="now-next-later" and j%3==i)]
            y=box["y"]+50
            for e in items[:6]:
                prims.append(P("text",x=x+12,y=y,w=box["w"]/3-36,h=40,text="• "+e.get("label",""),size=14,color=c["text"],font=theme["fonts"]["body"],lines=wrap_text("• "+e.get("label",""),box["w"]/3-36,14,2)))
                y+=44
        return prims
    # horizontal timeline / milestones
    y=box["y"]+box["h"]*0.5
    prims.append(P("line",x1=box["x"],y1=y,x2=box["x"]+box["w"],y2=y,stroke=c["grid"],width=3))
    for i,e in enumerate(events):
        x=box["x"]+box["w"]*(i+0.5)/len(events); above=i%2==0
        prims.append(P("ellipse",x=x-8,y=y-8,w=16,h=16,fill=status_color.get(e.get("status","planned"),c["accent"])))
        ly=y-70 if above else y+24
        prims.append(P("text",x=x-80,y=ly,w=160,h=20,text=e.get("label",""),size=14,color=c["text"],font=theme["fonts"]["body"],align="center",lines=wrap_text(e.get("label",""),160,14,2)))
        if e.get("date") or e.get("start"):
            prims.append(P("text",x=x-80,y=ly+22,w=160,h=18,text=e.get("date") or e.get("start"),size=12,color=c["muted"],font=theme["fonts"]["body"],align="center",lines=[e.get("date") or e.get("start")]))
    return prims

def kpi_primitives(kpis, box, theme):
    c=_colors(theme); n=max(1,len(kpis)); cw=box["w"]/n; prims=[]
    for i,k in enumerate(kpis[:6]):
        x=box["x"]+i*cw
        prims.append(P("rect",x=x+6,y=box["y"],w=cw-12,h=box["h"],fill=c["surface"],radius=theme.get("radius",12)))
        prims.append(P("text",x=x+18,y=box["y"]+14,w=cw-36,h=24,text=str(k.get("label","")),size=15,color=c["muted"],font=theme["fonts"]["body"],lines=wrap_text(str(k.get("label","")),cw-36,15,1)))
        val=str(k.get("value","")); vs=_scale(theme,"kpi",56)
        prims.append(P("text",x=x+18,y=box["y"]+box["h"]*0.35,w=cw-36,h=vs*1.2,text=val,size=vs,color=c["accent"],font=theme["fonts"]["heading"],bold=True,lines=wrap_text(val,cw-36,vs,1)))
        delta=str(k.get("delta") or "")
        if delta: prims.append(P("text",x=x+18,y=box["y"]+box["h"]-40,w=cw-36,h=22,text=delta,size=14,color=c["success"] if not delta.startswith("-") else c["danger"],font=theme["fonts"]["body"],lines=[delta]))
    return prims

def quiz_primitives(quiz, box, theme, slide_id):
    c=_colors(theme); prims=[]
    q=quiz.get("question",""); lines,qs=fit_text(q,box["w"],70,_scale(theme,"subtitle",26),16,True)
    prims.append(P("text",x=box["x"],y=box["y"],w=box["w"],h=qs*1.3*len(lines),text=q,size=qs,color=c["text"],font=theme["fonts"]["heading"],bold=True,lines=lines))
    y=box["y"]+qs*1.3*len(lines)+20
    for i,opt in enumerate(quiz.get("options") or []):
        h=52; prims.append(P("rect",x=box["x"],y=y,w=box["w"],h=h,fill=c["surface"],stroke=c["accent"],radius=8,quiz_option={"slide":slide_id,"index":i,"correct":bool(opt.get("correct")),"feedback":opt.get("feedback",""),"next":opt.get("next")}))
        prims.append(P("text",x=box["x"]+16,y=y+14,w=box["w"]-32,h=24,text=opt.get("text",""),size=18,color=c["text"],font=theme["fonts"]["body"],lines=wrap_text(opt.get("text",""),box["w"]-32,18,1),quiz_option={"slide":slide_id,"index":i}))
        y+=h+12
    return prims

def _image_data_uri(path):
    try:
        p=core.resolve_resource(path)
        data=p.read_bytes()
        if len(data)>12*1024*1024:
            return None
        ext=p.suffix.lower().lstrip('.') or 'png'
        mime={'jpg':'jpeg','jpeg':'jpeg','png':'png','gif':'gif','webp':'webp','svg':'svg+xml'}.get(ext,'png')
        return f"data:image/{mime};base64,"+base64.b64encode(data).decode('ascii')
    except Exception:
        return None

def render_svg(deck, slide, laid, theme, index, *, interactive=True, motion=False):
    canvas=laid["canvas"]; W,H=canvas["width"],canvas["height"]; c=_colors(theme)
    alt=slide.get("alt_text") or core.describe_visual(slide) or slide.get("title","Slide")
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t{index} d{index}" data-slide-id={quoteattr(slide["id"])}>']
    parts.append(f'<title id="t{index}">{escape(slide.get("title","Slide"))}</title><desc id="d{index}">{escape(alt)}</desc>')
    parts.append('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="'+c["muted"]+'"/></marker></defs>')
    if motion and (slide.get("transition") or {}).get("type","none")!="none":
        parts.append('<style>.motion{animation:fadein .45s ease-out}@keyframes fadein{from{opacity:0}to{opacity:1}}@media(prefers-reduced-motion:reduce){.motion{animation:none}}</style>')
    for p in laid["primitives"]:
        cls=' class="motion"' if motion else ''
        link=p.get("link") or {}
        href=None
        if link:
            href=("#slide-"+link["target_slide"]) if link.get("target_slide") else link.get("url")
        if href and not str(href).startswith(("#","https://","http://")):
            href=None
        open_a=f'<a href={quoteattr(href)}>' if href and interactive else ''
        close_a='</a>' if href and interactive else ''
        attrs=''
        if p.get("quiz_option") and interactive:
            q=p["quiz_option"]; attrs=f' data-quiz="{escape(q["slide"])}" data-option="{q["index"]}" data-correct="{str(q.get("correct",False)).lower()}" data-feedback={quoteattr(q.get("feedback",""))} data-next={quoteattr(q.get("next") or "")}'
        opacity=f' opacity="{p["opacity"]}"' if p.get("opacity") is not None else ''
        t=p["type"]
        if t=="rect":
            r=p.get("radius",0)
            parts.append(f'{open_a}<rect{cls}{attrs} x="{p["x"]}" y="{p["y"]}" width="{max(0,p["w"])}" height="{max(0,p["h"])}" rx="{r}" fill="{p.get("fill",c["surface"])}" stroke="{p.get("stroke","none")}"{opacity}/>{close_a}')
        elif t=="ellipse":
            parts.append(f'{open_a}<ellipse{cls}{attrs} cx="{p["x"]+p["w"]/2}" cy="{p["y"]+p["h"]/2}" rx="{max(0,p["w"]/2)}" ry="{max(0,p["h"]/2)}" fill="{p.get("fill",c["accent"])}" stroke="{p.get("stroke","none")}"{opacity}/>{close_a}')
        elif t=="line":
            marker=' marker-end="url(#arrow)"' if p.get("arrow") else ''
            parts.append(f'<line{cls} x1="{p["x1"]}" y1="{p["y1"]}" x2="{p["x2"]}" y2="{p["y2"]}" stroke="{p.get("stroke",c["grid"])}" stroke-width="{p.get("width",1)}"{marker}{opacity}/>')
        elif t=="image":
            uri=_image_data_uri(p.get("path",""))
            parts.append(f'<clipPath id="clip{index}-{len(parts)}"><rect x="{p["x"]}" y="{p["y"]}" width="{p["w"]}" height="{p["h"]}"/></clipPath>')
            if uri:
                par="xMidYMid slice" if p.get("fit","cover")=="cover" else "xMidYMid meet"
                parts.append(f'<image{cls} href="{uri}" x="{p["x"]}" y="{p["y"]}" width="{p["w"]}" height="{p["h"]}" preserveAspectRatio="{par}" clip-path="url(#clip{index}-{len(parts)-1})" role="img" aria-label={quoteattr(p.get("alt",""))}/>')
            else:
                parts.append(f'<rect x="{p["x"]}" y="{p["y"]}" width="{p["w"]}" height="{p["h"]}" fill="{c["surface"]}"/><text x="{p["x"]+12}" y="{p["y"]+28}" font-size="16" fill="{c["muted"]}">Image unavailable: {escape(p.get("path",""))}</text>')
        elif t=="text":
            size=p.get("size",22); lines=p.get("lines") or wrap_text(p.get("text",""),p.get("w",200),size,12)
            anchor={"center":"middle","right":"end"}.get(p.get("align","left"),"start")
            x=p["x"]+(p["w"]/2 if anchor=="middle" else p["w"] if anchor=="end" else 0)
            weight=' font-weight="700"' if p.get("bold") else ''
            style=' font-style="italic"' if p.get("italic") else ''
            direction=' direction="rtl"' if deck.get("direction")=="rtl" else ''
            parts.append(f'{open_a}<text{cls}{attrs} x="{x}" y="{p["y"]+size}" font-family={quoteattr(p.get("font",theme["fonts"]["body"]))} font-size="{size}" fill="{p.get("color",c["text"])}" text-anchor="{anchor}"{weight}{style}{direction}{opacity}>')
            for li,line in enumerate(lines):
                parts.append(f'<tspan x="{x}" dy="{0 if li==0 else size*1.22}">{escape(str(line))}</tspan>')
            parts.append(f'</text>{close_a}')
    parts.append('</svg>')
    return "".join(parts)

def render_html(deck, theme, layouts, *, title=None, handout=False, motion=True):
    c=_colors(theme); slides=deck["slides"]
    svgs=[render_svg(deck,s,l,theme,i,interactive=True,motion=motion) for i,(s,l) in enumerate(zip(slides,layouts))]
    css=f'''body{{margin:0;background:{c["surface"]};color:{c["text"]};font-family:{theme["fonts"]["body"]},Arial,sans-serif}}header{{padding:16px 24px;background:{c["background"]};border-bottom:1px solid {c["grid"]}}}main{{max-width:1280px;margin:0 auto;padding:24px}}.slide{{background:{c["background"]};margin:0 0 24px;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12)}}.slide svg{{width:100%;height:auto;display:block}}.notes{{padding:16px 24px;border-top:1px solid {c["grid"]};color:{c["muted"]};font-size:14px}}nav{{position:sticky;top:0;background:{c["background"]};padding:12px 24px;display:flex;gap:12px;align-items:center;border-bottom:1px solid {c["grid"]}}}button{{padding:8px 14px;border:1px solid {c["accent"]};background:{c["background"]};color:{c["accent"]};border-radius:6px;cursor:pointer}}.feedback{{padding:12px 24px;font-weight:600}}[data-quiz]{{cursor:pointer}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}'''
    js='''document.addEventListener('click',e=>{const el=e.target.closest('[data-quiz]');if(!el)return;const slide=el.closest('.slide');const fb=slide.querySelector('.feedback');const ok=el.getAttribute('data-correct')==='true';if(fb){fb.textContent=el.getAttribute('data-feedback')||(ok?'Correct.':'Try again.');fb.style.color=ok?'#15803D':'#B91C1C';}const next=el.getAttribute('data-next');if(next){const target=document.getElementById('slide-'+next);if(target)target.scrollIntoView({behavior:'smooth'});}});document.addEventListener('keydown',e=>{const slides=[...document.querySelectorAll('.slide')];if(!slides.length)return;const y=window.scrollY;let idx=slides.findIndex(s=>s.offsetTop>y+10);if(idx<0)idx=slides.length;if(e.key==='ArrowRight'||e.key==='PageDown'){e.preventDefault();slides[Math.min(idx,slides.length-1)].scrollIntoView({behavior:'smooth'});}if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();slides[Math.max(0,idx-2)].scrollIntoView({behavior:'smooth'});}});'''
    body=[]
    for i,(s,svg) in enumerate(zip(slides,svgs)):
        notes=f'<div class="notes"><strong>Speaker notes:</strong> {core.html_to_text(s.get("notes","")) or "No notes."}</div>' if handout else ''
        fb='<div class="feedback" aria-live="polite"></div>' if s.get("quiz") else ''
        body.append(f'<section class="slide" id="slide-{core.slugify(s["id"])}" aria-label="{escape(s.get("title","Slide"))}">{svg}{fb}{notes}</section>')
    return f'''<!doctype html><html lang="{escape(deck.get("language","en"))}" dir="{escape(deck.get("direction","ltr"))}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title or deck.get("title","Presentation"))}</title><style>{css}</style></head><body><header><h1>{escape(deck.get("title","Presentation"))}</h1><p>{escape(deck.get("audience",""))}</p></header><nav><button onclick="window.scrollTo({{top:0,behavior:'smooth'}})">Start</button><span>Use arrow keys or scroll. {len(slides)} slides.</span></nav><main>{''.join(body)}</main><script>{js}</script></body></html>'''
