"""A standalone standard-library consumer: no Oko, React, assets or Store imports."""
import hashlib
import http.client
import json
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

config=json.load(sys.stdin)
origin,token=config['url'],config['token']
with open(config['contract_path'],encoding='utf-8') as file:
    client_contract=json.load(file)
client_bytes=(json.dumps(client_contract,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
client_digest=hashlib.sha256(client_bytes).hexdigest()
assert '/api/v3/status' in client_contract['paths']
requests=[]


def send(method,path,body=None,action=None):
    requests.append((method,path))
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if action:
        headers['X-Oko-Action']=action
    request=Request(origin+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers=headers)
    try:
        response=urlopen(request,timeout=3)
    except HTTPError as error:
        response=error
    with response:
        return response.status,json.load(response)


code,status=send('GET','/api/v3/status')
assert code==200 and status['lifetime']=={'kind':'explicit'}
if status['api_version'].split('.')[0]!=str(config.get('major',client_contract['info']['version'].split('.')[0])):
    print(json.dumps({'state':'unsupported_major','requests':requests}))
    sys.exit(0)
code,initial=send('GET','/api/v3/settings/oko/global')
assert code==200
code,settings=send('PATCH','/api/v3/settings/oko/global',{'theses':False,'prompt_filters':''},'independent-settings')
assert code==200 and settings['effective']['theses']['value'] is False
assert send('PATCH','/api/v3/settings/oko/global',{'theses':'false'})[0]==422
assert send('GET','/api/v3/settings/oko/global')[1]==settings
assert send('POST','/api/v3/collect',{'scope':'all'},'independent-collect')[0]==200
for _ in range(100):
    code,page=send('GET','/api/v3/cards')
    cards=page['items']
    if cards and all(not card['loading'] for card in cards):
        break
    time.sleep(.02)
else:
    raise AssertionError(cards)
item=cards[0]['id']
code,material=send('GET','/api/v3/publication/'+item)
assert code==200 and material['text']
assert len(send('GET','/api/v3/cards')[1]['items'])==len(cards)
# Commit a request, then lose its response body before the client decodes it.
url=urlsplit(origin)
connection=http.client.HTTPConnection(url.hostname,url.port,timeout=3)
requests.append(('PATCH','/api/v3/settings/oko/global'))
connection.request('PATCH','/api/v3/settings/oko/global',body=b'{"originals":false}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','X-Oko-Action':'independent-lost'})
response=connection.getresponse()
response.close()
connection.close()
code,receipt=send('POST','/api/v3/command-observations',{'id':'independent-lost','context':{'operation':'settings_patch','target':{'scope':'oko','owner':'global'},'keys':['originals']},'result':'unknown'})
assert code==200 and receipt['outcome']=='succeeded'
assert send('GET','/api/v3/commands/independent-lost')[1]['outcome']=='succeeded'
assert send('GET','/api/v3/settings/oko/global')[1]['effective']['originals']['value'] is False
assert send('POST','/api/v3/stop',action='independent-stop')[0]==200
assert all(path.startswith('/api/v3/') and 'client-sessions' not in path for _,path in requests)
print(json.dumps({'state':'complete','api_version':status['api_version'],'client_contract_digest':client_digest,'requests':requests}))
