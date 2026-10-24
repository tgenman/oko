// A second Node reporter preserves TAP and records only advisory per-file costs.
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import type {TestEvent} from 'node:test/reporters';

export default async function* timings(source: AsyncIterable<TestEvent>) {
 const files:Record<string,number>={};
 for await(const event of source) {
  if(event.type!=='test:pass'&&event.type!=='test:fail')continue;
  const data=event.data;
  if(data.nesting!==0||!data.file)continue;
  const file=data.file.startsWith('file:')?fileURLToPath(data.file):data.file;
  if(!path.basename(file).startsWith('test_'))continue;
  const seconds=data.details.duration_ms/1000;
  if(Number.isFinite(seconds)&&seconds>=0)files[file]=(files[file]??0)+seconds;
 }
 yield JSON.stringify({version:1,files})+'\n';
}
