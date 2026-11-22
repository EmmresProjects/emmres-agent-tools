#!/usr/bin/env python3
"""Real CLI integration tests in an isolated future skill root; Python 3.13 (validated on 3.13.16)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

NAME='ultimate-agent-workbench'


def main():
    count=0
    with tempfile.TemporaryDirectory(prefix='workbench-check-') as tmp:
        root=Path(tmp);folder=root/NAME;folder.mkdir()
        for p in Path(__file__).resolve().parent.iterdir():
            if p.is_file():shutil.copy2(p,folder/p.name)
        (root/'SKILL.md').write_text('Test parent only; not delivered.\n')
        def call(*args,ok=True):
            nonlocal count
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
            p=subprocess.run([sys.executable,NAME+'/tools-workbench.py',*args],cwd=root,capture_output=True,text=True,timeout=20,env=env)
            count+=1
            if '--help' in args:
                assert p.returncode==0 and 'capabilities' in p.stdout;return
            data=json.loads(p.stdout)
            assert (p.returncode==0)==ok,(args,p.stdout,p.stderr)
            assert data['ok']==ok,(args,data)
            return data.get('result',data)
        def put(name,obj): (root/name).write_text(json.dumps(obj),encoding='utf-8')
        def process(op,params,output=None,ok=True):
            put('params.json',params)
            args=['process',op,'--input','params.json']
            if output:args+=['--output',output]
            return call(*args,ok=ok)
        call('--help');c=call('capabilities');assert len(c['capabilities'])==242
        call('status',ok=False);call('process','text-review','--input','missing.json',ok=False)
        (root/'sample.txt').write_text('First sentence. Second sentence.\nRepeated\nRepeated\n',encoding='utf-8')
        f=process('file-inspect',{'files':['sample.txt']});assert len(f['files'][0]['sha256'])==64
        a=process('text-review',{'input':'sample.txt'});assert a['duplicate-lines']==['Repeated']
        (root/'sample.txt').write_text('Changed.',encoding='utf-8');b=process('text-review',{'input':'sample.txt'});assert a['words']!=b['words']
        process('document-html',{'input':'sample.txt','title':'<Team>'},'result.html');assert '&lt;Team&gt;' in (root/'result.html').read_text()
        process('document-html',{'input':'sample.txt'},'result.html',ok=False)
        (root/'table.csv').write_text('name,value\na,2\nb,4\n');assert process('csv-analyze',{'input':'table.csv'})['columns']['value']['mean']==3
        put('valid.json',{'x':1});assert process('json-validate',{'input':'valid.json','required':['x']})['valid']
        assert not process('json-validate',{'input':'valid.json','required':['z']})['valid']
        (root/'code.py').write_text('eval("1+1")\n');assert process('python-audit',{'input':'code.py'})['findings']
        (root/'page.html').write_text('<html><img src="x"></html>');assert len(process('html-audit',{'input':'page.html'})['findings'])==2
        assert process('contrast',{'foreground':'#000000','background':'#ffffff'})['ratio']==21
        process('contrast',{'foreground':'invalid','background':'#ffffff'},ok=False)
        assert process('evidence-search',{'input':'sample.txt','query':'Changed'})['matches'][0]['line']==1
        process('zip-build',{'files':['sample.txt','valid.json']},'bundle.zip')
        with zipfile.ZipFile(root/'bundle.zip') as z:assert z.testzip() is None and len(z.namelist())==2
        process('zip-build',{'files':['bundle.zip']},'nested.zip',ok=False)
        (root/'input.md').write_text('## Unified\n### Alpha\nDoes A.\n### Beta\nDoes B.\n')
        assert len(process('catalog-ingest',{'input':'input.md'})['records'])==2
        (root/'input.html').write_text('<h2 class="x">Unified</h2><h3 data-a="1">Alpha</h3><p>Does A.</p>')
        assert process('catalog-ingest',{'input':'input.html'})['records'][0]['category']=='Unified'
        process('file-inspect',{'files':['../escape']},ok=False)
        call('plan','--input',NAME+'/asset-demo-request.json','--stages','2')
        call('run',ok=False)
        call('replan','--stages','3','--approve')
        assert call('status')['state']['stages']==3
        call('replan','--stages','2','--approve')
        assert call('status')['state']['stages']==2
        assert call('run')['label']=='Stage 1 of 2'
        call('run',ok=False)
        assert call('run','--signal','Continue')['status']=='complete'
        call('export','--output','receipt.json');assert json.loads((root/'receipt.json').read_text())['complete']
        call('cancel');call('recover','--approve')
        # Failure/retry, input change, dependency cycles and coverage rejection.
        request={'goal':'recovery','requirements':['valid'],'tasks':[{'id':'check','capability':c['capabilities'][0]['id'],'operation':'json-validate','params':{'input':'valid.json','required':['z']},'requirements':['valid'],'acceptance':'Required key exists'}]}
        put('request.json',request)
        call('--state','retry.sqlite','plan','--input','request.json','--approve')
        assert call('--state','retry.sqlite','run')['status']=='repair'
        put('valid.json',{'z':2});call('--state','retry.sqlite','recover','--approve')
        assert call('--state','retry.sqlite','run')['status']=='complete'
        request['tasks'][0]['depends']=['check'];put('request.json',request)
        call('--state','cycle.sqlite','plan','--input','request.json',ok=False)
        request['tasks'][0]['depends']=[];request['requirements'].append('missing');put('request.json',request)
        call('--state','coverage.sqlite','plan','--input','request.json',ok=False)
        # All 242 catalog entries are reachable through the actual evidence workflow interface.
        request={'goal':'catalog routing','tasks':[{'id':'t'+str(i),'capability':x['id'],'operation':'external','acceptance':'Reviewer attests supplied evidence'} for i,x in enumerate(c['capabilities'])]}
        put('external.json',request);call('--state','external.sqlite','plan','--input','external.json','--stages','1','--approve')
        put('evidence.json',{'passed':True,'reviewer':'Integration test','summary':'Synthetic routing test only, not domain verification','evidence':['sample.txt']})
        for i in range(242):
            blocked=call('--state','external.sqlite','run');assert blocked['status']=='blocked' and blocked['task']=='t'+str(i)
            call('--state','external.sqlite','submit','--task','t'+str(i),'--input','evidence.json','--approve')
        assert call('--state','external.sqlite','run')['status']=='complete'
        # Changed file stops execution; explicit refresh is the recovery path.
        request={'goal':'file integrity','tasks':[{'id':'file','capability':'universal-file-intake','operation':'file-inspect','params':{'files':['sample.txt']},'files':['sample.txt'],'acceptance':'Readable'}]}
        put('file-request.json',request);call('--state','file.sqlite','plan','--input','file-request.json','--approve')
        (root/'sample.txt').write_text('Replacement.');call('--state','file.sqlite','run',ok=False)
        call('--state','file.sqlite','recover','--refresh-files','--approve');assert call('--state','file.sqlite','run')['status']=='complete'
        files=list(folder.iterdir());assert all(p.is_file() for p in files)
        assert not (folder/'SKILL.md').exists()
        assert sum(p.name=='knowledge-readme.md' for p in files)==1
        assert all(p.name.startswith(('tools-','asset-','knowledge-')) for p in files)
    print(json.dumps({'ok':True,'cli-checks':count,'catalog-workflow-routes':242,'local-operations':11,'note':'Domain-specific generation, professional review and external integrations are NOT certified by these tests.'}))


if __name__=='__main__':main()
