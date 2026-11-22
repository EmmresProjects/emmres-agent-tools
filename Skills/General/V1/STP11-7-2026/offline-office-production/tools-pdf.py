"""Bounded PDF page, form, encryption and raster operations."""

def run(d,c):
    from pypdf import PdfReader, PdfWriter
    op=c.require(d,'action'); dest=c.require(d,'dest')
    if op=='form-create':
        from reportlab.pdfgen.canvas import Canvas
        fields=c.bounded(c.require(d,'fields',list)); seen=set()
        for f in fields:
            name=c.require(f,'name')
            if name in seen:c.fail('invalid_input','Duplicate form field.')
            seen.add(name)
            for key,default,upper in [('x',100,500),('y',700,760),('width',240,500),('height',20,100)]:
                v=f.get(key,default)
                if not isinstance(v,(int,float)) or not 1<=v<=upper:c.fail('invalid_input','Invalid form geometry.')
            if f.get('x',100)+f.get('width',240)>595 or f.get('y',700)+f.get('height',20)>842:c.fail('invalid_input','Field exceeds page.')
        def save(p):
            canvas=Canvas(str(p),pagesize=(595,842)); canvas.setTitle(d.get('title','Form'))
            canvas.drawString(40,800,d.get('title','Form'))
            for f in fields:
                x,y=f.get('x',100),f.get('y',700)
                canvas.drawString(40,y+5,f.get('label',f['name']))
                canvas.acroForm.textfield(name=f['name'],tooltip=f.get('label',f['name']),x=x,y=y,width=f.get('width',240),height=f.get('height',20),value=f.get('value',''))
            canvas.showPage();canvas.save()
        c.write(dest,save);return {'artifact':dest,'fields':len(fields)}
    if op=='merge':
        sources=c.bounded(c.require(d,'sources',list))
        if not sources:c.fail('invalid_input','At least one input required.')
    else:sources=[c.require(d,'source')]
    readers=[]
    for source in sources:
        p=c.path(source,True);r=PdfReader(p)
        if r.is_encrypted:
            if not d.get('password') or not r.decrypt(d['password']):c.fail('password','PDF requires a correct password.')
        if len(r.pages)>1000:c.fail('limit','PDF exceeds 1000 pages.')
        readers.append(r)
    if op in ('render','redact'):
        import pymupdf
        p=c.path(sources[0],True)
        with pymupdf.open(p) as doc:
            if doc.is_encrypted and not doc.authenticate(d.get('password','')):c.fail('password','Incorrect PDF password.')
            if op=='render':
                page=d.get('page',1);dpi=d.get('dpi',96)
                if not isinstance(page,int) or not 1<=page<=len(doc):c.fail('invalid_input','Page out of range.')
                if not isinstance(dpi,int) or not 36<=dpi<=150:c.fail('limit','DPI must be 36–150.')
                pg=doc[page-1]
                if pg.rect.width*pg.rect.height*(dpi/72)**2>25000000:c.fail('limit','Raster exceeds 25 megapixels.')
                if not dest.endswith('.png'):c.fail('format','Render destination must be .png.')
                c.write(dest,lambda p:pg.get_pixmap(dpi=dpi,alpha=False).save(p))
                return {'artifact':dest,'page':page,'dpi':dpi}
            c.need_approval('Redaction permanently removes matched text and modifies document content.')
            terms=c.bounded(c.require(d,'terms',list))
            if not terms or any(not isinstance(t,str) or not t.strip() for t in terms):c.fail('invalid_input','Nonempty text terms required.')
            hits=0
            for pg in doc:
                c.check()
                for term in terms:
                    for rect in pg.search_for(term):pg.add_redact_annot(rect,fill=(0,0,0));hits+=1
                pg.apply_redactions()
            doc.set_metadata({})
            if hits==0:c.fail('no_matches','No searchable matches. OCR or visual/manual redaction may be needed.')
            def save(p):
                doc.save(p,garbage=4,deflate=True)
                with pymupdf.open(p) as check:
                    text='\n'.join(pg.get_text() for pg in check)
                    if any(t.casefold() in text.casefold() for t in terms):c.fail('redaction_failed','Text remains; output not committed.')
            c.write(dest,save)
            return {'artifact':dest,'redacted_regions':hits,'warning':'Text-layer verification only. Not certification of images, attachments, hidden objects or forensic privacy.'}
    w=PdfWriter()
    if op=='merge':
        if sum(len(r.pages) for r in readers)>1000:c.fail('limit','Merged PDF exceeds 1000 pages.')
        for r in readers:c.check();w.append(r)
    elif op=='select':
        pages=c.bounded(c.require(d,'pages',list));r=readers[0]
        if not pages or any(not isinstance(n,int) or not 1<=n<=len(r.pages) for n in pages):c.fail('invalid_input','pages must contain valid 1-based page numbers.')
        for n in pages:w.add_page(r.pages[n-1])
    else:
        w.clone_document_from_reader(readers[0])
        if op=='rotate':
            degrees=d.get('degrees',90)
            if degrees not in (90,180,270):c.fail('invalid_input','Rotation must be 90, 180 or 270 degrees.')
            for page in w.pages:page.rotate(degrees)
        elif op=='encrypt':
            c.need_approval('Encryption is a security-sensitive action.')
            password=c.require(d,'new_password')
            if len(password)<12:c.fail('password','Use a password of at least 12 characters.')
            w.encrypt(password,algorithm='AES-256')
        elif op=='decrypt':c.need_approval('Removing encryption is a security-sensitive action.')
        elif op=='metadata-clear':
            c.need_approval('Removing metadata modifies provenance.')
            w.metadata=None
        elif op=='form-fill':
            values=c.require(d,'values',dict); fields=readers[0].get_fields() or {}
            if any(k not in fields for k in values):c.fail('invalid_input','Unknown form field name.')
            if any(not isinstance(v,str) for v in values.values()):c.fail('invalid_input','Field values must be strings.')
            for pg in w.pages:w.update_page_form_field_values(pg,values,auto_regenerate=False)
        else:c.fail('unsupported','Unknown PDF action: '+op)
    c.write(dest,lambda p:w.write(str(p)))
    return {'artifact':dest,'pages':len(w.pages),'action':op,'warning':'PDF modifications can invalidate existing digital signatures. Signature preservation is not supported.'}
