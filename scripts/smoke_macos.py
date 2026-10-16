"""Verify the distributed backend and native window using temporary, synthetic data."""
import base64
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'dist'/'Meu Dinheiro.app'


def api(base,path,payload=None,token=None):
    headers={'Content-Type':'application/json'}
    if token: headers['X-Local-Session']=token
    request=Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
    try:
        with urlopen(request,timeout=15) as response:
            return response.status,json.load(response)
    except HTTPError as error:
        return error.code,json.load(error)


def main():
    with tempfile.TemporaryDirectory(prefix='finance-packaged-backend-') as folder:
        with open(Path(folder)/'test.log','wb') as log:
            backend=subprocess.Popen([str(APP/'Contents'/'Resources'/'backend'/'meu-dinheiro-server'),
                '--port','0','--db',str(Path(folder)/'financas.db'),'--no-browser'],stdout=subprocess.PIPE,stderr=log)
            try:
                base=None;deadline=time.monotonic()+45
                while time.monotonic()<deadline:
                    if select.select([backend.stdout],[],[],1)[0]:
                        line=backend.stdout.readline().decode()
                        if line.startswith('Finanças disponível em '): base=line.strip().split(' em ',1)[1];break
                    if backend.poll() is not None: raise RuntimeError('Packaged backend exited; inspect temporary test log')
                assert base,'Backend did not become ready'
                assert api(base,'/api/v1/summary')[0]==401
                status,created=api(base,'/api/v1/local-auth/register',{'username':'testuser','display_name':'Teste sintético',
                    'password':'synthetic packaged password','password_confirmation':'synthetic packaged password'})
                assert status==201
                token=created['token']
                assert api(base,'/api/v1/transactions',{'kind':'expense','description':'Compra fictícia','amount':'12',
                    'category':'Mercado','occurred_on':'2026-10-15'},token)[0]==201
                status,inspected=api(base,'/api/v1/imports/inspect',{'filename':'sample_statement.pdf',
                    'content_base64':base64.b64encode((ROOT/'tests'/'fixtures'/'sample_statement.pdf').read_bytes()).decode()},token)
                assert status==200 and inspected['format']=='pdf',(status,inspected)
                assert api(base,'/api/v1/local-auth/demo',{})[0]==201
                print('PACKAGED_BACKEND_OK: password signup, protected API, transaction and PDF reader',flush=True)
            finally:
                backend.terminate()
                try:backend.wait(timeout=15)
                except subprocess.TimeoutExpired:backend.kill();backend.wait()
    with tempfile.TemporaryDirectory(prefix='finance-native-window-') as folder:
        native=subprocess.Popen([str(APP/'Contents'/'MacOS'/'MeuDinheiro')],
            env={**os.environ,'MEU_DINHEIRO_DATA_DIR':folder,'MEU_DINHEIRO_SMOKE_TEST':'1'},
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            output,error=native.communicate(timeout=45)
            assert native.returncode==0 and b'DESKTOP_SMOKE_OK' in output,(native.returncode,error.decode()[-800:])
            print('NATIVE_WINDOW_OK: WebKit loaded the real login page and closed its backend',flush=True)
        finally:
            if native.poll() is None:native.terminate();native.wait(timeout=15)


if __name__=='__main__':main()
