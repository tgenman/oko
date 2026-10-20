"""Synthetic HTTP contention probe, without product instrumentation."""
import asyncio,json,time,sys
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from fastapi.testclient import TestClient
from tests.support.performance_seed import seed
from tests.support.reading_cost import traced_app,request_sql
from tests.support.reader import HEADERS

def percentiles(values):
 s=sorted(values)
 return dict(n=len(s),p50=s[len(s)//2],p95=s[min(len(s)-1,int(len(s)*.95))],max=s[-1]) if s else dict(n=0)

with TemporaryDirectory(prefix='oko-contention-') as tmp:
 root=Path(tmp);seed(root,100,False);app=traced_app(root)
 with TestClient(app,headers=HEADERS) as client:
  identity=client.get('/api/v3/cards').json()[0]['id']
  async def exercise(tabs):
   original=app.state.runtime._actions;waits=[];lags=[];times=[];done=False
   class Probe:
    async def __aenter__(self):
     start=time.perf_counter();await original.__aenter__()
     if request_sql.get() is not None:waits.append((time.perf_counter()-start)*1000)
     return self
    async def __aexit__(self,*args):return await original.__aexit__(*args)
   async def ticker():
    while not done:
     start=time.perf_counter();await asyncio.sleep(.005);lags.append(max(0,(time.perf_counter()-start)*1000-5))
   async def worker():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers=HEADERS) as http:
     for _ in range(30):
      start=time.perf_counter();response=await http.get('/api/v3/reading/materials/'+identity)
      assert response.status_code==200
      times.append((time.perf_counter()-start)*1000)
   app.state.runtime._actions=Probe();task=asyncio.create_task(ticker())
   try:await asyncio.gather(*(worker() for _ in range(tabs)))
   finally:done=True;await task;app.state.runtime._actions=original
   return dict(clients=tabs,requests=percentiles(times),lock_wait=percentiles(waits),loop_lag=percentiles(lags))
  result=[client.portal.call(exercise,n) for n in [1,5,20]]
  Path(sys.argv[1]).write_text(json.dumps(result,indent=2));print(json.dumps(result))
