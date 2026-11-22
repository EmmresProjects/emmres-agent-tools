"""Native authoring, text extraction, structural/security inspection."""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET


def create(d, c):
    kind = c.require(d, 'format').lower()
    dest = c.require(d, 'dest')
    if not dest.endswith('.' + kind): c.fail('format', 'Destination extension must match format.')
    title = d.get('title', 'Untitled')
    if not isinstance(title, str): c.fail('invalid_input', 'title must be text.')
    if kind == 'docx':
        from docx import Document
        from docx.shared import Inches
        doc = Document()
        doc.core_properties.title = title
        doc.add_heading(title, 0)
        for block in c.bounded(d.get('blocks', [])):
            c.check()
            if not isinstance(block, dict): c.fail('invalid_input', 'Each block must be an object.')
            typ = block.get('type', 'paragraph')
            if typ == 'heading': doc.add_heading(c.require(block, 'text'), max(1, min(9, int(block.get('level', 1)))))
            elif typ == 'paragraph': doc.add_paragraph(c.require(block, 'text'))
            elif typ == 'bullet': doc.add_paragraph(c.require(block, 'text'), style='List Bullet')
            elif typ == 'table':
                rows = c.bounded(c.require(block, 'rows', list))
                if not rows or not rows[0] or len(rows[0]) > 100: c.fail('invalid_input', 'Table requires 1–100 columns.')
                table = doc.add_table(rows=0, cols=len(rows[0])); table.style = 'Table Grid'
                for row in rows:
                    if not isinstance(row, list) or len(row) != len(rows[0]): c.fail('invalid_input', 'Table rows must be rectangular.')
                    for cell, value in zip(table.add_row().cells, row): cell.text = str(value)
            else: c.fail('unsupported', 'Unsupported DOCX block: ' + str(typ))
        result = c.write(dest, lambda p: doc.save(p))
    elif kind == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.chart import BarChart, Reference
        from openpyxl.worksheet.datavalidation import DataValidation
        wb = Workbook(); wb.remove(wb.active)
        sheets = c.bounded(c.require(d, 'sheets', list))
        if not sheets or len(sheets) > 100: c.fail('limit', 'Use 1–100 sheets.')
        names = set()
        for sheet in sheets:
            name = c.require(sheet, 'name')
            if not name or len(name) > 31 or re.search(r'[\\/*?:\[\]]', name) or name.casefold() in names: c.fail('invalid_input', 'Invalid or duplicate sheet name.')
            names.add(name.casefold()); ws = wb.create_sheet(name)
            for row in c.bounded(c.require(sheet, 'rows', list)):
                c.check()
                if not isinstance(row, list) or len(row) > 100: c.fail('limit', 'Rows must have at most 100 cells.')
                if any(isinstance(v, (dict, list)) for v in row): c.fail('invalid_input', 'Cells must be scalar values.')
                for v in row:
                    if isinstance(v,str) and v.startswith('='): 
                        if not d.get('allow_formulas'): c.fail('approval_required', 'Set allow_formulas and approve to author formulas.')
                        c.need_approval('Formula authoring can introduce external or active expressions.')
                        if re.search(r'\[|https?://|WEBSERVICE|DDE|HYPERLINK|RTD|CALL|EXEC',v,re.I): c.fail('unsafe_formula','External/active formula prohibited.')
                ws.append(row)
            for cell in ws[1]: cell.font = Font(bold=True)
            ws.freeze_panes = 'A2'; ws.auto_filter.ref = ws.dimensions
            for rule in c.bounded(sheet.get('validations', [])):
                values = c.require(rule, 'values', list)
                if not values or any(not isinstance(x,str) or any(ch in x for ch in '",\n') for x in values): c.fail('invalid_input','List values must be simple strings.')
                formula = '"' + ','.join(values) + '"'
                if len(formula)>255: c.fail('limit','Validation list exceeds Excel inline limit.')
                dv = DataValidation(type='list', formula1=formula); dv.errorTitle='Invalid value'; dv.error='Choose a listed value.'; dv.showErrorMessage=True
                ws.add_data_validation(dv); dv.add(c.require(rule, 'range'))
            if sheet.get('chart'):
                if ws.max_column<2 or ws.max_row<2: c.fail('invalid_input','Charts require a header and two columns.')
                chart=BarChart(); chart.title=sheet.get('chart_title', name)
                chart.add_data(Reference(ws,min_col=2,max_col=ws.max_column,min_row=1,max_row=ws.max_row),titles_from_data=True)
                chart.set_categories(Reference(ws,min_col=1,min_row=2,max_row=ws.max_row)); ws.add_chart(chart, 'E2')
        result=c.write(dest,lambda p:wb.save(p))
    elif kind == 'pptx':
        from pptx import Presentation
        prs=Presentation()
        for slide in c.bounded(c.require(d,'slides',list)):
            s=prs.slides.add_slide(prs.slide_layouts[1]); s.shapes.title.text=c.require(slide,'title')
            tf=s.placeholders[1].text_frame; tf.clear()
            for i,text in enumerate(c.bounded(slide.get('bullets',[]))):
                if not isinstance(text,str): c.fail('invalid_input','Bullets must be strings.')
                (tf.paragraphs[0] if i==0 else tf.add_paragraph()).text=text
            if 'notes' in slide: s.notes_slide.notes_text_frame.text=c.require(slide,'notes')
        if not prs.slides: c.fail('invalid_input','At least one slide required.')
        prs.core_properties.title=title; result=c.write(dest,lambda p:prs.save(p))
    elif kind == 'pdf':
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
        from reportlab.lib.styles import getSampleStyleSheet
        from xml.sax.saxutils import escape
        if any(ord(x)>255 for x in title): c.fail('font','PDF title requires Latin text with the built-in font.')
        styles=getSampleStyleSheet(); story=[Paragraph(escape(title),styles['Title'])]
        for block in c.bounded(d.get('blocks',[])):
            typ=block.get('type','paragraph')
            if typ=='table':
                rows=c.bounded(c.require(block,'rows',list))
                if not rows or not rows[0] or len(rows[0])>100 or any(not isinstance(r,list) or len(r)!=len(rows[0]) for r in rows):c.fail('invalid_input','PDF tables must be nonempty and rectangular with at most 100 columns.')
                if any(any(ord(ch)>255 for ch in str(v)) for r in rows for v in r):c.fail('font','Built-in PDF table fonts require Latin text.')
                story.append(Table([[str(v) for v in r] for r in rows]))
            elif typ in ('paragraph','heading','bullet'):
                text=c.require(block,'text')
                if any(ord(x)>255 for x in text+title): c.fail('font','Built-in PDF fonts support Latin text only; use native OOXML for multilingual content.')
                story.append(Paragraph(escape(text),styles['Heading2' if typ=='heading' else 'BodyText']))
            else: c.fail('unsupported','Unsupported PDF block type.')
            story.append(Spacer(1,8))
        result=c.write(dest,lambda p:SimpleDocTemplate(str(p), title=title).build(story))
    else: c.fail('unsupported','Authoring supports docx, xlsx, pptx, pdf.')
    return {'artifact': result, 'format':kind, 'warning':'No Office compatibility, accessibility conformance, formula recalculation or archival certification is implied.'}


def extract(d,c):
    value=c.require(d,'source'); p=c.path(value,True); ext=p.suffix.lower()
    if ext in ('.docx','.pptx','.xlsx'): c.zip_check(p)
    if ext=='.docx':
        from docx import Document
        doc=Document(p); text='\n'.join([x.text for x in doc.paragraphs]+['\t'.join(x.text for x in row.cells) for t in doc.tables for row in t.rows])
    elif ext=='.pptx':
        from pptx import Presentation
        text='\n'.join(f'Slide {i+1}\n'+'\n'.join(s.text for s in slide.shapes if s.has_text_frame) for i,slide in enumerate(Presentation(p).slides))
    elif ext=='.xlsx':
        from openpyxl import load_workbook
        wb=load_workbook(p,read_only=True,data_only=False,keep_links=False); parts=[]
        for s in wb:
            if s.max_row*s.max_column>100000: c.fail('limit','Workbook exceeds extraction cell limit.')
            parts.append(s.title+'\n'+'\n'.join('\t'.join('' if v is None else str(v) for v in row) for row in s.iter_rows(values_only=True)))
        wb.close(); text='\n'.join(parts)
    elif ext=='.pdf':
        from pypdf import PdfReader
        r=PdfReader(p)
        if r.is_encrypted: c.fail('encrypted','Decrypt to a new file first.')
        if len(r.pages)>1000: c.fail('limit','PDF exceeds 1000 pages.')
        text='\n'.join(page.extract_text() or '' for page in r.pages)
    elif ext in ('.txt','.md','.csv','.html'): text=p.read_text(encoding='utf-8')
    else:c.fail('unsupported','Unsupported extraction format.')
    if 'dest' in d: c.write(d['dest'],lambda q:q.write_text(text,encoding='utf-8'))
    return {'text':text,'characters':len(text),'scope':'Body text only; extraction is not a structure-preserving conversion.'}


def inspect(d,c):
    p=c.path(c.require(d,'source'),True); ext=p.suffix.lower(); findings=[]; stats={}
    if ext in ('.docx','.pptx','.xlsx'):
        c.zip_check(p)
        with zipfile.ZipFile(p) as z:
            names=z.namelist()
            if '[Content_Types].xml' not in names:c.fail('corrupt','Missing OOXML content types.')
            expected={'.docx':'word/document.xml','.pptx':'ppt/presentation.xml','.xlsx':'xl/workbook.xml'}[ext]
            if expected not in names:c.fail('corrupt','Missing primary OOXML part.')
            stats['parts']=len(names)
            for name in names:
                c.check()
                if 'vba' in name.lower() or 'activex' in name.lower() or '/embeddings/' in name:
                    findings.append({'severity':'critical','code':'active-content','part':name})
                if name.endswith(('.xml','.rels')):
                    raw=z.read(name)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): c.fail('unsafe_xml','DTD/entities prohibited.')
                    root=ET.fromstring(raw)
                    for e in root.iter():
                        local=e.tag.rsplit('}',1)[-1]
                        if e.attrib.get('TargetMode')=='External':findings.append({'severity':'warning','code':'external-relationship','part':name,'target':e.attrib.get('Target')})
                        if local in ('ins','del','comment'):findings.append({'severity':'warning','code':'review-content','part':name})
                        if local=='docPr' and not e.attrib.get('descr'):findings.append({'severity':'warning','code':'missing-alt-text','part':name})
                        if local=='f':stats['formulas']=stats.get('formulas',0)+1
    elif ext=='.pdf':
        from pypdf import PdfReader
        r=PdfReader(p); stats={'encrypted':r.is_encrypted}
        if r.is_encrypted:findings.append({'severity':'critical','code':'encrypted-uninspected'})
        else:
            stats['pages']=len(r.pages); root=r.trailer['/Root']
            for key in ('/OpenAction','/AA'):
                if key in root:findings.append({'severity':'critical','code':'active-action','key':key})
            if '/Names' in root:findings.append({'severity':'warning','code':'named-content-needs-review'})
            if '/StructTreeRoot' not in root:findings.append({'severity':'warning','code':'untagged-pdf'})
            stats['forms']=len(r.get_fields() or {})
            stats['metadata']=dict(r.metadata or {})
            for page in r.pages:
                c.check()
                if '/AA' in page:findings.append({'severity':'critical','code':'page-action'})
                for ref in page.get('/Annots',[]):
                    ann=ref.get_object()
                    if '/A' in ann: findings.append({'severity':'warning','code':'annotation-action'})
    else:c.fail('unsupported','Inspect supports DOCX, PPTX, XLSX, PDF.')
    return {'source':d['source'],'statistics':stats,'findings':findings,'release':'blocked' if findings else 'manual-review-required','scope':'Structural heuristics only; not malware, accessibility, standards, signature or layout certification.'}
