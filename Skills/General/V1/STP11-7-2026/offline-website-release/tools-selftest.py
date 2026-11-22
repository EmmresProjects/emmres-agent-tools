#!/usr/bin/env python3
"""Offline end-to-end regression tests using isolated temporary inputs and outputs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL=Path(__file__).with_name('tools-website.py')

def call(*args):
    p=subprocess.run([sys.executable,str(TOOL),*map(str,args)],capture_output=True,text=True,timeout=20)
    try: value=json.loads(p.stdout if p.stdout else p.stderr)
    except json.JSONDecodeError: value=None
    return p,value

class WebsiteTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.p=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def dump(self,name,data):
        p=self.p/name; p.write_text(json.dumps(data),encoding='utf-8'); return p
    def test_help_capabilities_catalog(self):
        p=subprocess.run([sys.executable,str(TOOL),'--help'],capture_output=True,text=True,timeout=20)
        self.assertEqual(p.returncode,0); self.assertIn('verify-artifacts',p.stdout)
        p,v=call('capabilities');self.assertEqual(p.returncode,0);self.assertEqual(len(v['implemented']),5)
        p,v=call('catalog'); self.assertEqual(v['count'],1238)
        p,v=call('catalog','--category','19','--query','keyboard');self.assertGreater(v['count'],0)
        self.assertTrue(all(x['category_number']==19 for x in v['items']))
    def test_scaffold_audit_changes_and_recovery(self):
        config=self.dump('site.json',{'title':'Title & Test','description':'Description','heading':'First','body':'<script>alert(1)</script>'})
        site=self.p/'site'; p,v=call('scaffold','--input',config,'--site',site)
        self.assertEqual(p.returncode,0,p.stderr); text=(site/'index.html').read_text();self.assertIn('&lt;script&gt;',text)
        p,v=call('audit','--site',site);self.assertEqual(p.returncode,0);self.assertEqual(v['summary']['errors'],0)
        (site/'index.html').write_text('<html><img src="missing.png"></html>')
        p,v=call('audit','--site',site); self.assertEqual(p.returncode,1);self.assertGreaterEqual(v['summary']['errors'],3)
        self.assertTrue({'html-lang','title','image-alt','broken-local-reference'} <= {x['code'] for x in v['findings']})
        p,v=call('scaffold','--input',config,'--site',site);self.assertEqual(p.returncode,2)
        config.write_text(json.dumps({'title':'Changed','description':'Another','heading':'Second','body':'Safe'}))
        site2=self.p/'site2'; p,v=call('scaffold','--input',config,'--site',site2);self.assertEqual(p.returncode,0);self.assertIn('Second',(site2/'index.html').read_text()); self.assertNotIn('First',(site2/'index.html').read_text())
    def test_artifacts(self):
        root=self.p/'artifacts';root.mkdir(); (root/'x.txt').write_text('hello')
        digest=hashlib.sha256(b'hello').hexdigest();manifest=self.dump('manifest.json',{'artifacts':[{'path':'x.txt','sha256':digest}]})
        p,v=call('verify-artifacts','--root',root,'--manifest',manifest);self.assertEqual(p.returncode,0);self.assertTrue(v['all_verified'])
        (root/'x.txt').write_text('changed');p,v=call('verify-artifacts','--root',root,'--manifest',manifest);self.assertEqual(p.returncode,1);self.assertEqual(v['artifacts'][0]['status'],'mismatch')
        (root/'x.txt').unlink(); p,v=call('verify-artifacts','--root',root,'--manifest',manifest);self.assertEqual(v['artifacts'][0]['status'],'missing')
        manifest.write_text(json.dumps({'artifacts':[{'path':'../outside','sha256':digest}]}));p,v=call('verify-artifacts','--root',root,'--manifest',manifest);self.assertEqual(p.returncode,2)
    def test_readiness(self):
        gates=['offline_restore','build_tests','browser_journeys','accessibility','performance','security_privacy','seo','backup_restore','rollback','dns_tls_hosting','monitoring','release_approval']
        data={'gates':{g:{'status':'pass','evidence':'Recorded review','owner':'Reviewer'} for g in gates}}
        path=self.dump('gates.json',data);p,v=call('readiness','--input',path);self.assertEqual(p.returncode,0);self.assertTrue(v['ready'])
        data['gates']['rollback']['status']='unknown';path.write_text(json.dumps(data));p,v=call('readiness','--input',path);self.assertEqual(p.returncode,1);self.assertFalse(v['ready'])
        data['gates']['rollback']={'status':'pass'};path.write_text(json.dumps(data));p,v=call('readiness','--input',path);self.assertEqual(p.returncode,2)
    def test_invalid_and_output_approval(self):
        p,v=call('audit','--site',self.p/'missing');self.assertEqual(p.returncode,2);self.assertIn('error',v)
        p,v=call('readiness');self.assertEqual(p.returncode,2);self.assertIn('error',v)
        x=self.p/'broken.json';x.write_text('{');p,v=call('readiness','--input',x);self.assertEqual(p.returncode,2)
        report=self.p/'report.json';p,v=call('capabilities','--output',report);self.assertEqual(p.returncode,0); self.assertEqual(json.loads(report.read_text())['operation'],'capabilities')
        p,v=call('capabilities','--output',report);self.assertEqual(p.returncode,2)
        p,v=call('capabilities','--output',report,'--replace');self.assertEqual(p.returncode,0)
        p,v=call('readiness','--input',x,'--output',x,'--replace');self.assertEqual(p.returncode,2)

if __name__=='__main__': unittest.main(verbosity=2)
