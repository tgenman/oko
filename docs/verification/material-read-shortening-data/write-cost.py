"""Cost of a feed-scoped settings write on the same synthetic library."""
import json,sys,time
from pathlib import Path
from tempfile import TemporaryDirectory
from tests.support.reading_cost import library
from oko.storage.store import Store
from oko.subscriptions.subscriptions import Overrides
results=[]
for count in [26,100]:
 with TemporaryDirectory(prefix='oko-write-cost-') as tmp:
  feeds,_=library(Path(tmp),[count]*4);store=Store(Path(tmp),lambda _:None)
  queries=[];steps=[]
  store.db.set_trace_callback(queries.append)
  store.db.set_progress_handler(lambda:steps.append(1) or 0,100)
  start=time.perf_counter()
  store.subscriptions.update_settings('feed',feeds[0],Overrides(originals=False))
  store.settings_changed('feed',feeds[0])
  elapsed=(time.perf_counter()-start)*1000
  results.append(dict(materials=count*4,selected=count,sql=len(queries),vm=len(steps)*100,ms=elapsed))
  store.db.set_trace_callback(None);store.db.set_progress_handler(None,0);store.db.close()
Path(sys.argv[1]).write_text(json.dumps(results,indent=2));print(results)
