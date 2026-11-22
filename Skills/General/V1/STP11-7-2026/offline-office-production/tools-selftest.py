#!/usr/bin/env python3
"""End-to-end tests in an isolated future skill root; no network."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode=True

class Suite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='office-test-')
        cls.root=Path(cls.temp.name);cls.folder=Path(__file__).resolve().parent.name
        shutil.copytree(Path(__file__).resolve().parent,cls.root/cls.folder,ignore=shutil.ignore_patterns('__pycache__'))
        (cls.root/'SKILL.md').write_text('# Test parent skill\n')
        cls.entry=cls.folder+'/tools-office.py'
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def cli(self,*args,ok=True):
        p=subprocess.run([sys.executable,self.entry,*args],cwd=self.root,text=True,capture_output=True,timeout=45)
        if args==('--help',):self.assertEqual(p.returncode,0);return p.stdout
        try:r=json.loads(p.stdout)
        except Exception:self.fail(p.stderr+'\n'+p.stdout)
        self.assertEqual(p.returncode==0,ok,r)
        return r
    def runop(self,op,d=None,ok=True,approve=False,output=None):
        args=['run',op]
        if d is not None:
            (self.root/'request.json').write_text(json.dumps(d),encoding='utf-8');args+=['--input','request.json']
        if approve:args+=['--approve']
        if output:args+=['--output',output]
        return self.cli(*args,ok=ok)
    def author(self,fmt,dest,text='Alpha confidential',**extra):
        d={'format':fmt,'dest':dest,'title':'Test','blocks':[{'type':'paragraph','text':text}]}
        if fmt=='pptx':d['slides']=[{'title':text,'bullets':['One','Two'],'notes':'Speaker notes'}]
        if fmt=='xlsx':d['sheets']=[{'name':'Data','rows':[['Label','Value'],['A',2],['B',3]],'chart':True,'validations':[{'range':'A2:A3','values':['A','B']}]}]
        d.update(extra);return self.runop('create',d)
    def test_01_help_catalog(self):
        self.cli('--help');r=self.cli('capabilities');self.assertEqual(r['catalog_count'],480)
        r=self.cli('catalog');self.assertEqual(len(r['entries']),480)
        self.assertEqual(len({x['category'] for x in r['entries']}),32)
        self.cli('catalog','--id','c01-01');self.cli('catalog','--id','missing',ok=False)
    def test_02_author_reopen(self):
        for fmt in ('docx','pptx','xlsx','pdf'):
            name='author.'+fmt;self.author(fmt,name)
            self.runop('extract',{'source':name,'dest':fmt+'.txt'})
            self.runop('inspect',{'source':name})
        from docx import Document
        from pptx import Presentation
        from openpyxl import load_workbook
        from pypdf import PdfReader
        self.assertIn('Alpha',Document(self.root/'author.docx').paragraphs[1].text)
        self.assertEqual(len(Presentation(self.root/'author.pptx').slides),1)
        wb=load_workbook(self.root/'author.xlsx');self.assertEqual(wb.active['B2'].value,2);self.assertEqual(len(wb.active._charts),1)
        self.assertEqual(len(wb.active.data_validations.dataValidation),1);wb.close()
        self.assertEqual(len(PdfReader(self.root/'author.pdf').pages),1)
    def test_03_changed_input_compare(self):
        self.author('docx','left.docx',text='First');self.author('docx','right.docx',text='Second')
        r=self.runop('compare',{'left':'left.docx','right':'right.docx','dest':'diff.txt'})
        self.assertFalse(r['result']['equal']);self.assertIn('+Second',r['result']['diff'])
        self.assertTrue(self.runop('compare',{'left':'left.docx','right':'left.docx'})['result']['equal'])
    def test_04_pdf_pages_render(self):
        self.author('pdf','one.pdf');self.author('pdf','two.pdf',text='Bravo')
        self.runop('pdf',{'action':'merge','sources':['one.pdf','two.pdf'],'dest':'merged.pdf'})
        self.runop('pdf',{'action':'select','source':'merged.pdf','pages':[2,1,2],'dest':'selected.pdf'})
        self.runop('pdf',{'action':'rotate','source':'selected.pdf','degrees':90,'dest':'rotated.pdf'})
        self.runop('pdf',{'action':'render','source':'rotated.pdf','page':1,'dpi':72,'dest':'page.png'})
        from PIL import Image
        with Image.open(self.root/'page.png') as im:im.verify()
        from pypdf import PdfReader
        r=PdfReader(self.root/'rotated.pdf');self.assertEqual(len(r.pages),3);self.assertEqual(r.pages[0].rotation,90)
        self.runop('pdf',{'action':'select','source':'one.pdf','pages':[0],'dest':'bad.pdf'},ok=False)
    def test_05_forms(self):
        self.runop('pdf',{'action':'form-create','dest':'form.pdf','fields':[{'name':'name','label':'Name'}]})
        self.runop('pdf',{'action':'form-fill','source':'form.pdf','dest':'filled.pdf','values':{'name':'Ada'}})
        from pypdf import PdfReader
        self.assertEqual(PdfReader(self.root/'filled.pdf').get_fields()['name']['/V'],'Ada')
        self.runop('pdf',{'action':'form-fill','source':'form.pdf','dest':'badform.pdf','values':{'missing':'X'}},ok=False)
    def test_06_security(self):
        self.author('pdf','secure.pdf')
        req={'action':'encrypt','source':'secure.pdf','dest':'encrypted.pdf','new_password':'test-password-long'}
        self.runop('pdf',req,ok=False);self.runop('pdf',req,approve=True)
        self.runop('pdf',{'action':'decrypt','source':'encrypted.pdf','dest':'plain.pdf','password':'wrong'},ok=False,approve=True)
        self.runop('pdf',{'action':'decrypt','source':'encrypted.pdf','dest':'plain.pdf','password':'test-password-long'},approve=True)
        self.assertIn('confidential',self.runop('extract',{'source':'plain.pdf'})['result']['text'])
        self.runop('pdf',{'action':'metadata-clear','source':'plain.pdf','dest':'clean.pdf'},approve=True)
        self.runop('pdf',{'action':'redact','source':'secure.pdf','dest':'redacted.pdf','terms':['confidential']},approve=True)
        self.assertNotIn('confidential',self.runop('extract',{'source':'redacted.pdf'})['result']['text'])
        self.runop('pdf',{'action':'redact','source':'secure.pdf','dest':'none.pdf','terms':['not-present']},approve=True,ok=False)
    def test_07_merge(self):
        self.author('docx','template.docx',text='Dear {{name}}, ref {{ref}}.')
        self.runop('mail-merge',{'template':'template.docx','records':[{'name':'Ada','ref':12},{'name':'Lin','ref':13}],'dest_prefix':'letters'})
        self.assertIn('Dear Lin',self.runop('extract',{'source':'letters-0002.docx'})['result']['text'])
        self.runop('mail-merge',{'template':'template.docx','records':[{'name':'Missing'}],'dest_prefix':'badletters'},ok=False)
    def test_08_parse(self):
        (self.root/'catalog.html').write_text('<style>.x {color:red}</style><h2>Category: Records</h2><div>1. Skill Tool Name: Backup</div><p>Description: Preserve files offline.</p><div>2. Skill Name: Restore</div><p>Description: Recover safely.</p>')
        r=self.runop('parse-catalog',{'source':'catalog.html','dest':'normalized.json'})
        self.assertEqual(r['result']['count'],2);self.assertEqual(r['result']['capabilities'][0]['category'],'Records')
        (self.root/'catalog.md').write_text('## Category: Office\n1. **Skill Name: Writer**\n   Description: Author native documents.\n')
        self.assertEqual(self.runop('parse-catalog',{'source':'catalog.md'})['result']['count'],1)
        self.author('docx','source-catalog.docx',text='Skill Name: Compose\nDescription: Create editable documents.')
        self.assertEqual(self.runop('parse-catalog',{'source':'source-catalog.docx'})['result']['count'],1)
    def test_08b_category_and_plain(self):
        (self.root/'category.txt').write_text('PDF Rendering, Editing, and Page Operations')
        self.assertEqual(self.runop('parse-catalog',{'source':'category.txt'})['result']['count'],15)
        (self.root/'plain.txt').write_text('Category: Custom\n1. Prepare records\nDescription: Keep verified records.\n2. Recover records\nDescription: Restore safely.')
        self.assertEqual(self.runop('parse-catalog',{'source':'plain.txt'})['result']['count'],2)
        (self.root/'single.txt').write_text('Compose documents\nGenerate editable reports from structured data.')
        self.assertEqual(self.runop('parse-catalog',{'source':'single.txt'})['result']['count'],1)

    def test_09_baseline_assess(self):
        r=self.runop('baseline');self.assertFalse(r['result']['network_used'])
        r=self.runop('assess',{'ids':['c01-01','c32-15']});self.assertEqual(len(r['result']['entries']),2)
        self.assertTrue(all(x['release']=='blocked' for x in r['result']['entries']))
        self.runop('assess',{'ids':['fake']},ok=False)
    def test_10_records(self):
        (self.root/'record.txt').write_text('record')
        self.runop('records',{'action':'backup','sources':['record.txt'],'dest':'backup.zip'})
        self.runop('records',{'action':'verify','source':'backup.zip'})
        self.runop('records',{'action':'restore','source':'backup.zip','dest_prefix':'restored'},ok=False)
        self.runop('records',{'action':'restore','source':'backup.zip','dest_prefix':'restored'},approve=True)
        a=self.runop('records',{'action':'digest','source':'record.txt'})['result']['sha256']
        b=self.runop('records',{'action':'digest','source':'restored/record.txt'})['result']['sha256'];self.assertEqual(a,b)
    def test_11_gate(self):
        self.author('docx','gate.docx')
        self.assertFalse(self.runop('gate',{'source':'gate.docx','evidence':{}})['result']['released'])
        checks=json.loads((self.root/self.folder/'asset-release-policy.json').read_text())['required_checks']
        self.assertTrue(self.runop('gate',{'source':'gate.docx','evidence':dict.fromkeys(checks,True)})['result']['released'])
    def test_12_batch_recovery(self):
        req={'jobs':[{'operation':'create','request':{'format':'docx','dest':'batch.docx','blocks':[]}},{'operation':'extract','request':{'source':'missing.docx'}}]}
        r=self.runop('batch',req,ok=False,output='batch-report.json');self.assertEqual(r['result']['resume_from'],1)
        req['jobs'][1]['request']['source']='batch.docx';req['resume_from']=1
        r=self.runop('batch',req);self.assertTrue(r['result']['completed']);self.assertEqual(len(r['result']['results']),1)
        json.loads((self.root/'batch-report.json').read_text())
    def test_13_invalid_missing_and_paths(self):
        self.runop('create',{},ok=False);self.runop('extract',{'source':'missing'},ok=False)
        self.cli('run','create',ok=False);self.cli('run','unknown',ok=False)
        (self.root/'broken.json').write_text('{')
        self.cli('run','create','--input','broken.json',ok=False)
        self.runop('extract',{'source':'../escape.docx'},ok=False)
        self.runop('create',{'format':'docx','dest':'/absolute.docx'},ok=False)
        self.runop('create',{'format':'docx','dest':'mismatch.pdf'},ok=False)
        self.author('docx','overwrite.docx');self.author('docx','overwrite-second.docx')
        self.runop('create',{'format':'docx','dest':'overwrite.docx','title':'Replacement'},ok=False)
        self.runop('create',{'format':'docx','dest':'overwrite.docx','title':'Replacement'},approve=True)
    def test_14_formula(self):
        sheets=[{'name':'Calc','rows':[['A'],[2],['=SUM(A2:A2)']]}]
        self.runop('create',{'format':'xlsx','dest':'formula.xlsx','sheets':sheets},ok=False)
        self.runop('create',{'format':'xlsx','dest':'formula.xlsx','sheets':sheets,'allow_formulas':True},approve=True)
        self.assertIn('=SUM',self.runop('extract',{'source':'formula.xlsx'})['result']['text'])
        sheets[0]['rows'][2]=['=WEBSERVICE("https://example.invalid")']
        self.runop('create',{'format':'xlsx','dest':'unsafe.xlsx','sheets':sheets,'allow_formulas':True},approve=True,ok=False)
    def test_15_adapters_missing_or_unapproved(self):
        self.author('docx','adapter.docx')
        r=self.runop('adapter',{'adapter':'libreoffice-pdf','source':'adapter.docx','dest':'adapter.pdf'},ok=False)
        self.assertIn(r['error']['code'],('integration_unavailable','approval_required'))
        from PIL import Image
        Image.new('RGB',(20,20),'white').save(self.root/'ocr.png')
        r=self.runop('adapter',{'adapter':'tesseract-text','source':'ocr.png','dest':'ocr.txt'},ok=False)
        self.assertIn(r['error']['code'],('integration_unavailable','approval_required'))
    def test_16_malicious_archive(self):
        import zipfile
        with zipfile.ZipFile(self.root/'evil.zip','w') as z:z.writestr('../escape.txt','bad')
        self.runop('records',{'action':'verify','source':'evil.zip'},ok=False)
        self.runop('records',{'action':'restore','source':'evil.zip','dest_prefix':'evil'},ok=False,approve=True)
    def test_17_active_content(self):
        import zipfile
        self.author('docx','active-base.docx')
        shutil.copyfile(self.root/'active-base.docx',self.root/'active.docx')
        with zipfile.ZipFile(self.root/'active.docx','a') as z:z.writestr('word/vbaProject.bin',b'not executed')
        r=self.runop('inspect',{'source':'active.docx'});self.assertTrue(any(x['code']=='active-content' for x in r['result']['findings']))
    def test_18_package_contract(self):
        folder=self.root/self.folder
        self.assertFalse((folder/'SKILL.md').exists());self.assertEqual(len(list(folder.glob('knowledge-*'))),1)
        for p in folder.iterdir():
            self.assertTrue(p.is_file(),p);self.assertTrue(p.name.startswith(('tools-','knowledge-','asset-')),p)
        self.runop('create',json.loads((folder/'asset-example-request.json').read_text()))

if __name__=='__main__':unittest.main(verbosity=2)
