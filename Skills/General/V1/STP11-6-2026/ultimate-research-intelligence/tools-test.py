#!/usr/bin/env python3
"""Self-contained CLI integration tests using temporary input documents."""
import json
import pathlib
import subprocess
import sys
import tempfile

MAIN=pathlib.Path(__file__).with_name('tools-main.py')
def call(*args,valid=True):
    result=subprocess.run([sys.executable,str(MAIN),*map(str,args)],capture_output=True,text=True,timeout=30)
    assert (result.returncode==0)==valid,(args,result.stderr)
    return json.loads(result.stdout if valid else result.stderr)

def main():
    assert call('capabilities')['count']==350
    assert call('capabilities','--skill','35.10')['count']==1
    with tempfile.TemporaryDirectory() as tmp:
        p=pathlib.Path(tmp)
        (p/'a.html').write_text('<html><title>Agency bulletin</title><nav>Noise</nav><article><p>Solar output was 12 units in 2024.</p><a href="https://example.org/method">Method</a></article></html>')
        (p/'b.csv').write_text('year,units\n2024,12\n')
        c={'question':'What was solar output in 2024?','claims':['Solar output was 12 units in 2024.'],'must_cover':['solar output','cost'],'source_classes':['official','academic'],'sources':[{'path':'a.html','type':'official'},{'path':'b.csv','type':'academic'}]}
        config=p/'config.json';config.write_text(json.dumps(c))
        assert len(call('plan','--input',config)['result']['queries'])>=3
        assert len(call('discover','--input',config)['leads'])==1
        extracted=call('extract','--input',config,'--include-text')
        assert len(extracted['sources'])==2 and extracted['sources'][1]['tables'][1]==['2024','12']
        assert 'Noise' not in extracted['sources'][0]['text']
        assert call('verify','--input',config)['result']['findings'][0]['evidence']
        assert any(x['value']=='cost' for x in call('audit','--input',config)['result']['gaps'])
        assert call('synthesize','--input',config)['synthesis'][0]['citations']
        assert call('run','--input',config,'--category','35')['plan']['catalog_selection']
        state=p/'state.json';assert len(call('monitor','--input',config,'--state',state)['result']['events'])==2
        assert call('monitor','--input',config,'--state',state,valid=False)['error']
        assert not call('monitor','--input',config,'--state',state,'--approve-overwrite')['result']['events']
        (p/'a.html').write_text('<p>Solar output was 15 units in 2024.</p>')
        assert call('monitor','--input',config,'--state',state,'--approve-overwrite')['result']['events'][0]['change']=='updated'
        assert '15 units' in call('run','--input',config)['synthesis'][0]['citations'][0]['passage']
        output=p/'output.json';assert call('audit','--input',config,'--output',output)['ok'] and json.loads(output.read_text())['ok']
        assert call('audit','--input',config,'--output',output,valid=False)['error']
        assert call('audit','--input',config,'--output',output,'--approve-overwrite')['ok']
        c['sources']=[{'path':'missing.txt'}];config.write_text(json.dumps(c))
        assert call('run','--input',config,valid=False)['error']
        c['sources']=[];config.write_text(json.dumps(c))
        assert call('run','--input',config)['result']['completion']=='incomplete'
        assert call('run','--input',p/'missing.json',valid=False)['error']
    print('PASS: catalog, plan, discovery, HTML/CSV extraction, verification, audit, synthesis, run, monitor/recovery, invalid/missing inputs, output approval, input change')
if __name__=='__main__':main()
