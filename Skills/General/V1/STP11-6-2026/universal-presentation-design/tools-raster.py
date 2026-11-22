"""Pillow-based PNG previews, raster PDF, GIF motion previews, and contact sheets.

PDF output is rasterized from the same layout primitives as SVG; PPTX and SVG remain editable.
"""
from __future__ import annotations
import io, math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import tools_core as core
import tools_render as render

def _font(size, bold=False):
    try:
        return ImageFont.load_default(size=max(6,int(size)))
    except TypeError:
        return ImageFont.load_default()

def _color(v, default="#000000"):
    try:
        return core.hex_to_rgb(v or default)
    except Exception:
        return core.hex_to_rgb(default)

def render_png(laid, *, scale=1.0, background="#FFFFFF"):
    canvas=laid["canvas"]; W=int(canvas["width"]*scale); H=int(canvas["height"]*scale)
    img=Image.new("RGB",(W,H),_color(background)); d=ImageDraw.Draw(img,"RGBA")
    for p in laid["primitives"]:
        t=p["type"]
        if t=="rect":
            x,y,w,h=[int(p.get(k,0)*scale) for k in ("x","y","w","h")]
            fill=_color(p.get("fill"),background)+(int(255*p.get("opacity",1)),) if p.get("opacity") is not None else _color(p.get("fill"),background)
            stroke=_color(p.get("stroke"),p.get("fill",background)) if p.get("stroke") else None
            r=int(p.get("radius",0)*scale)
            if r>0:
                d.rounded_rectangle([x,y,x+max(1,w),y+max(1,h)],radius=r,fill=fill,outline=stroke)
            else:
                d.rectangle([x,y,x+max(1,w),y+max(1,h)],fill=fill,outline=stroke)
        elif t=="ellipse":
            x,y,w,h=[int(p.get(k,0)*scale) for k in ("x","y","w","h")]
            fill=_color(p.get("fill"),"#2563EB")
            if p.get("opacity") is not None: fill=fill+(int(255*p["opacity"]),)
            d.ellipse([x,y,x+max(1,w),y+max(1,h)],fill=fill,outline=_color(p.get("stroke"),p.get("fill","#2563EB")) if p.get("stroke") else None)
        elif t=="line":
            x1,y1,x2,y2=[int(p.get(k,0)*scale) for k in ("x1","y1","x2","y2")]
            d.line([x1,y1,x2,y2],fill=_color(p.get("stroke"),"#999999"),width=max(1,int(p.get("width",1)*scale)))
            if p.get("arrow"):
                ang=math.atan2(y2-y1,x2-x1); L=10*scale
                d.polygon([(x2,y2),(x2-L*math.cos(ang-0.4),y2-L*math.sin(ang-0.4)),(x2-L*math.cos(ang+0.4),y2-L*math.sin(ang+0.4))],fill=_color(p.get("stroke"),"#999999"))
        elif t=="image":
            x,y,w,h=[int(p.get(k,0)*scale) for k in ("x","y","w","h")]
            try:
                src=Image.open(core.resolve_resource(p.get("path",""))).convert("RGBA")
                if p.get("fit","cover")=="cover":
                    ratio=max(w/src.width,h/src.height); nw,nh=int(src.width*ratio),int(src.height*ratio)
                    src=src.resize((nw,nh),Image.Resampling.LANCZOS)
                    fx=p.get("focal_x",0.5); fy=p.get("focal_y",0.5)
                    left=int((nw-w)*fx); top=int((nh-h)*fy)
                    src=src.crop((left,top,left+w,top+h))
                else:
                    src.thumbnail((w,h),Image.Resampling.LANCZOS)
                img.paste(src,(x,y),src)
            except Exception:
                d.rectangle([x,y,x+w,y+h],fill=_color("#F4F6F8"))
                d.text((x+8,y+8),"Image unavailable",font=_font(16*scale),fill=_color("#4B5563"))
        elif t=="text":
            size=p.get("size",22)*scale; font=_font(size,p.get("bold",False)); color=_color(p.get("color"),"#111827")
            lines=p.get("lines") or render.wrap_text(p.get("text",""),p.get("w",200),p.get("size",22),12)
            x=p["x"]*scale; y=p["y"]*scale
            for li,line in enumerate(lines):
                tw=d.textlength(line,font=font)
                xx=x
                if p.get("align")=="center": xx=x+(p["w"]*scale-tw)/2
                elif p.get("align")=="right": xx=x+p["w"]*scale-tw
                d.text((xx,y+li*size*1.22),str(line).replace('•','-').replace('…','...'),font=font,fill=color)
    return img

def write_pdf(images, path, *, dpi=96):
    if not images:
        raise core.ToolError("NO_IMAGES","PDF export requires at least one rendered slide.")
    rgb=[im.convert("RGB") for im in images]
    rgb[0].save(path,save_all=True,append_images=rgb[1:],resolution=dpi,format="PDF")
    return path

def write_gif(images, path, *, durations_ms=None, loop=0):
    if not images:
        raise core.ToolError("NO_IMAGES","GIF export requires at least one rendered slide.")
    durations=durations_ms or [1200]*len(images)
    pal=[im.convert("P",palette=Image.Palette.ADAPTIVE) for im in images]
    pal[0].save(path,save_all=True,append_images=pal[1:],duration=durations,loop=loop,format="GIF",optimize=True)
    return path

def write_contact_sheet(images, path, *, cols=4, thumb_width=320):
    if not images:
        raise core.ToolError("NO_IMAGES","Contact sheet requires at least one slide.")
    thumbs=[]
    for im in images:
        t=im.copy(); t.thumbnail((thumb_width,thumb_width*9//16),Image.Resampling.LANCZOS); thumbs.append(t)
    rows=math.ceil(len(thumbs)/cols); cw=thumb_width+16; ch=max(t.height for t in thumbs)+32
    sheet=Image.new("RGB",(cols*cw,rows*ch),"#FFFFFF"); d=ImageDraw.Draw(sheet)
    for i,t in enumerate(thumbs):
        x=(i%cols)*cw+8; y=(i//cols)*ch+8
        sheet.paste(t,(x,y)); d.text((x,y+t.height+4),f"Slide {i+1}",font=_font(14),fill="#111827")
    sheet.save(path,format="PNG")
    return path
