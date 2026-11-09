/* Real HTTP + Chromium scenario; requires a disposable OKO_TEST_POSTGRES_URL.
 * Run from the specified Oko checkout; all sources/data below are synthetic.
 * The Python child owns and drops the temporary PostgreSQL schema at teardown.
 */
import {test} from 'node:test';
import {spawn, execFileSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {once} from 'node:events';
import * as fs from 'node:fs/promises';
import * as os from 'node:os';
import * as path from 'node:path';
import {createRequire} from 'node:module';
import {productionReader} from './http-reader';
import {openAdmin, openReading} from './admin-navigation';
import {artifactPath} from './artifacts';

const repo=process.cwd();
const requireFromRepo=createRequire(path.join(repo,'package.json'));
const {expect}=requireFromRepo('playwright/test') as typeof import('playwright/test');
const python=process.env.OKO_PYTHON || path.join(repo,'.venv/bin/python');

const seed=String.raw`
import json, os, sys
from pathlib import Path
import psycopg
from tests.support.postgres import isolated_database
from tests.support.storage import Store
from tests.support.collection_acceptance import discovered
from tests.support.source import publication_packet
from tests.support.synthetic_material import material as Material
from tests.support.legacy_source import Publication
from tests.support.subscription_action import subscription_action
from tests.support.storage_action import storage_action
from tests.support.job_claim import claim_job
from oko.storage.transaction import transaction
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS
from oko.storage.publication_owners import owned_ids
from oko.subscriptions.subscriptions import AddFeed

root=Path(sys.argv[1]); dsn_path=Path(sys.argv[2]); mode=sys.argv[3] if len(sys.argv)>3 else 'damaged'; feeds={}; items={}
base=os.environ['OKO_TEST_POSTGRES_URL']
with isolated_database(base) as dsn:
    with psycopg.connect(dsn,autocommit=True) as installer:
        migrate(installer,MIGRATIONS)
    dsn_path.write_text(dsn); dsn_path.chmod(0o600)
    store=Store(root,lambda _:None,database_url=dsn)
    try:
        for label in ('bad','good'):
            feed=subscription_action(store,'add_feed',AddFeed(url=f'https://{label}.test/feed',title=f'{label.title()} feed',folder='Synthetic folder',verification='synthetic'))['id']
            feeds[label]=feed
            with transaction(store.db):
                store.jobs.enqueue_discovery(feed)
            job=claim_job(store,store.clock(),feeds=[feed])
            assert job and job.kind=='discover'
            pub=Publication(label,f'https://{label}.test/article',100)
            discovered(store,[publication_packet(pub,Material(f'{label.title()} publication',f'{label.title()} synthetic body'),needs_fetch=False)],'{}',job,feed)
            items[label]=store.db.execute(owned_ids('= %s'),(feed,feed)).fetchone()[0]
        with store.db:
            store.subscriptions.history.enroll(feeds['bad'],[items['bad']],True)
            if mode=='valid':
                store.subscriptions.history.saved(items['bad'],True)
            else:
                store.db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',('{broken',feeds['bad']))
        storage_action(store,'set_work_enabled',mode=='valid')
    finally:
        store.db.close()
    print(json.dumps(dict(feeds=feeds,items=items)),flush=True)
    sys.stdin.readline()
`;

const snapshot=String.raw`
import json,sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
with psycopg.connect(Path(sys.argv[1]).read_text().strip(),row_factory=dict_row) as db:
    feed=sys.argv[2]; item=sys.argv[3]
    row=db.execute('SELECT * FROM initial_history WHERE feed=%s',(feed,)).fetchone()
    members=db.execute('SELECT * FROM history_members WHERE feed=%s ORDER BY item',(feed,)).fetchall()
    publication=db.execute('SELECT id,content,feed_id,viewed FROM publications WHERE id=%s',(item,)).fetchone()
    print(json.dumps(dict(history=row,members=members,publication=publication),sort_keys=True,default=str))
`;

test('damaged saved history leaves reading available and does not offer destructive recovery', {timeout:90000},async t=>{
 if(!process.env.OKO_TEST_POSTGRES_URL)throw new Error('Set OKO_TEST_POSTGRES_URL to a disposable synthetic PostgreSQL');
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'oko-damaged-history-browser-'));
 const dsnFile=path.join(tmp,'database-url');
 const child=spawn(python,['-u','-c',seed,path.join(tmp,'seed'),dsnFile],{cwd:repo,stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-12000);});
 const lines=createInterface({input:child.stdout});
 let reader:Awaited<ReturnType<typeof productionReader>>|undefined;
 try {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const first=await Promise.race([
   once(lines,'line').then(values=>String(values[0])),
   once(child,'exit').then(values=>{throw new Error(`Synthetic seed exited (${values[0]}): ${stderr}`);}),
   new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error(`Synthetic seed timed out: ${stderr}`)),30000);}),
  ]).finally(()=>clearTimeout(timer));
  const fixture=JSON.parse(first) as {feeds:{bad:string;good:string};items:{bad:string;good:string}};
  const dbSnapshot=()=>JSON.parse(execFileSync(python,['-c',snapshot,dsnFile,fixture.feeds.bad,fixture.items.bad],{cwd:repo,encoding:'utf8'}));
  const before=dbSnapshot();
  expect(before.history.selection).toBe('{broken');
  reader=await productionReader(t,{collection:false,databaseUrlFile:dsnFile});
  const {page,api,origin,token}=reader;
  const historyWrites:string[]=[];
  const errors:string[]=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/v3/feeds/')&&url.pathname.endsWith('/history')&&request.method()!=='GET')historyWrites.push(request.method()+' '+url.pathname);});
  await page.goto(origin+'/#token='+token);
  await expect(page.locator(`[data-feed="${fixture.feeds.bad}"]`)).toBeVisible();
  await expect(page.locator(`[data-feed="${fixture.feeds.good}"]`)).toBeVisible();
  await page.locator(`[data-feed="${fixture.feeds.bad}"]`).click();
  await expect(page.locator('#cards [data-card]')).toHaveCount(1);
  await page.locator('#cards [data-card]').click();
  await expect(page.locator('#title')).toContainText('Bad publication');
  await expect(page.locator('#text')).toContainText('Bad synthetic body');
  await page.screenshot({path:artifactPath('oko-damaged-history-reading.png'),fullPage:true});
  const status=await api('status/details');
  const problems=status.problems.current.filter(p=>p.code==='history_invalid');
  expect(problems).toHaveLength(1);expect(problems[0]!.recovery).toEqual([]);
  expect(problems[0]!.affected.some(a=>a.kind==='feed'&&a.feed_id===fixture.feeds.bad)).toBe(true);
  await openAdmin(page);await page.locator('#status-toggle').click();
  const problem=page.locator('.status-problem').filter({hasText:'Saved History Needs Attention'});
  await expect(problem).toBeVisible();
  await expect(problem).toContainText('Saved History Needs Attention');
  await expect(problem.locator('.status-action')).toHaveCount(0);
  await page.screenshot({path:artifactPath('oko-damaged-history-problem.png'),fullPage:true});
  await page.locator('#status-close').click();await openReading(page);
  await page.locator('#feeds-open').click();
  await page.locator('#feed-tree').getByRole('button',{name:'Bad feed',exact:true}).click();
  await page.getByRole('button',{name:'Connection and Options',exact:true}).click();
  await expect(page.locator('#history-progress')).toHaveText('Saved history needs attention');
  await expect(page.locator('#rss-load-history-start')).toBeDisabled();
  await page.screenshot({path:artifactPath('oko-damaged-history-settings.png'),fullPage:true});
  expect(historyWrites).toEqual([]);expect(errors).toEqual([]);
  expect(dbSnapshot()).toEqual(before);
 } finally {
  if(reader)await reader.close();
  child.stdin.end('\n');
  if(child.exitCode===null && child.signalCode===null){
   const exited=once(child,'exit');const timer=setTimeout(()=>child.kill('SIGKILL'),5000);
   try {await exited;} finally {clearTimeout(timer);}
  }
  lines.close();await fs.rm(tmp,{recursive:true,force:true});
 }
});

const corrupt=String.raw`
import sys
from pathlib import Path
import psycopg
with psycopg.connect(Path(sys.argv[1]).read_text().strip()) as db:
    db.execute('UPDATE initial_history SET selection=%s WHERE feed=%s',('{broken',sys.argv[2]))
`;

test('open feed settings switch to damaged when details report a newly damaged history', {timeout:90000},async t=>{
 if(!process.env.OKO_TEST_POSTGRES_URL)throw new Error('Set OKO_TEST_POSTGRES_URL to a disposable synthetic PostgreSQL');
 const tmp=await fs.mkdtemp(path.join(os.tmpdir(),'oko-live-history-browser-'));
 const dsnFile=path.join(tmp,'database-url');
 // collection:false disables construction of external RSS adapters while
 // runtime is enabled, making the initial enabled/disabled controls meaningful.
 const child=spawn(python,['-u','-c',seed,path.join(tmp,'seed'),dsnFile,'valid'],{cwd:repo,stdio:['pipe','pipe','pipe']});
 let stderr='';child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-12000);});
 const lines=createInterface({input:child.stdout});
 let reader:Awaited<ReturnType<typeof productionReader>>|undefined;
 try {
  let timer:ReturnType<typeof setTimeout>|undefined;
  const first=await Promise.race([
   once(lines,'line').then(values=>String(values[0])),
   once(child,'exit').then(values=>{throw new Error(`Synthetic seed exited (${values[0]}): ${stderr}`);}),
   new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error(`Synthetic seed timed out: ${stderr}`)),30000);}),
  ]).finally(()=>clearTimeout(timer));
  const fixture=JSON.parse(first) as {feeds:{bad:string;good:string};items:{bad:string;good:string}};
  const dbSnapshot=()=>JSON.parse(execFileSync(python,['-c',snapshot,dsnFile,fixture.feeds.bad,fixture.items.bad],{cwd:repo,encoding:'utf8'}));
  const before=dbSnapshot();expect(before.history.selection).not.toBe('{broken');
  reader=await productionReader(t,{collection:false,databaseUrlFile:dsnFile});
  const {page,api,origin,token}=reader;
  const historyWrites:string[]=[],errors:string[]=[];
  let afterDamage=false,detailsWithProblem=0;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/v3/feeds/')&&url.pathname.endsWith('/history')&&request.method()!=='GET')historyWrites.push(request.method()+' '+url.pathname);});
  page.on('response',response=>{
   if(!afterDamage||new URL(response.url()).pathname!=='/api/v3/status/details'||response.status()!==200)return;
   void response.json().then(body=>{if(body.problems?.current?.some((p:{code:string})=>p.code==='history_invalid'))detailsWithProblem++;}).catch(()=>{});
  });
  await page.goto(origin+'/#token='+token);
  await expect(page.locator(`[data-feed="${fixture.feeds.bad}"]`)).toBeVisible();
  await expect(page.locator(`[data-feed="${fixture.feeds.good}"]`)).toBeVisible();
  await page.locator('#feeds-open').click();
  await page.locator('#feed-tree').getByRole('button',{name:'Bad feed',exact:true}).click();
  await page.getByRole('button',{name:'Connection and Options',exact:true}).click();
  const progress=page.locator('#history-progress');
  await expect(progress).toBeVisible();
  await expect(progress).not.toHaveText('Saved history needs attention');
  await expect(page.locator('#rss-load-history-start')).toBeEnabled();
  await expect(page.locator('#rss-load-history-count')).toBeEnabled();
  // Mutate only the isolated synthetic schema; no reader route is called.
  afterDamage=true;
  execFileSync(python,['-c',corrupt,dsnFile,fixture.feeds.bad],{cwd:repo,encoding:'utf8'});
  const damaged=dbSnapshot();expect(damaged.history.selection).toBe('{broken');
  expect(damaged.members).toEqual(before.members);
  // Keep the same dialog and subpage mounted: no reload, close, or reselection.
  await expect.poll(()=>detailsWithProblem,{timeout:15000}).toBeGreaterThan(0);
  await expect(progress).toHaveText('Saved history needs attention',{timeout:15000});
  await expect(page.locator('#rss-load-history-start')).toBeDisabled();
  await expect(page.locator('#rss-load-history-count')).toBeDisabled();
  await expect(page.locator('#rss-load-history-stop')).toBeHidden();
  await page.screenshot({path:artifactPath('oko-damaged-history-live-settings.png'),fullPage:true});
  expect(historyWrites).toEqual([]);expect(errors).toEqual([]);
  expect(dbSnapshot()).toEqual(damaged);
  await page.locator('#feeds-close').click();
  await expect(page.locator(`[data-feed="${fixture.feeds.bad}"]`)).toBeVisible();
  await expect(page.locator(`[data-feed="${fixture.feeds.good}"]`)).toBeVisible();
  await page.locator(`[data-feed="${fixture.feeds.bad}"]`).click();
  await expect(page.locator('#cards [data-card]')).toHaveCount(1);
  await page.locator('#cards [data-card]').click();
  await expect(page.locator('#text')).toContainText('Bad synthetic body');
  await page.screenshot({path:artifactPath('oko-damaged-history-live-reading.png'),fullPage:true});
  expect(dbSnapshot()).toEqual(damaged);
 } finally {
  if(reader)await reader.close();
  child.stdin.end('\n');
  if(child.exitCode===null&&child.signalCode===null){
   const exited=once(child,'exit');const timer=setTimeout(()=>child.kill('SIGKILL'),5000);
   try{await exited;}finally{clearTimeout(timer);}
  }
  lines.close();await fs.rm(tmp,{recursive:true,force:true});
 }
});
