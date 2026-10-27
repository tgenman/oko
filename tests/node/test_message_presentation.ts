import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decodeJson,errorMessage,ReaderError} from '../../frontend/src/api/errors';
import {problemConsequence} from '../../frontend/src/status/recovery';
import type {Problem} from '../../frontend/src/api/generated/types.gen';

test('known error codes do not depend on server wording and keep diagnostic details', async()=>{
 for(const message of ['Старый текст', 'Новая формулировка']){
  let error:unknown;
  try {await decodeJson(new Response(JSON.stringify({code:'runtime_stopped',message,details:null,diagnostic_id:'d',command_id:null}),{status:409}));}
  catch(value){error=value;}
  assert.ok(error instanceof ReaderError);
  assert.equal(error.message,message);
  assert.equal(error.diagnosticId,'d');
  assert.equal(errorMessage(error),'Oko stopped');
 }
});

test('unknown and broad errors preserve original diagnostic text',()=>{
 const error=new ReaderError('Детали от источника · сохранены');
 error.code='source_connection_invalid';
 assert.equal(errorMessage(error),error.message);
 error.code='future_code';
 assert.equal(errorMessage(error),error.message);
});

test('problem consequence uses known code and preserves unknown details',()=>{
 const base={id:'p',code:'material_incomplete',command_id:null,title:'x',message:'details',consequence:'Произвольный текст',affected:[],recovery:[],created_at:'2026-01-01T00:00:00Z',state:'current'} satisfies Problem;
 assert.equal(problemConsequence(base),'The available portion can still be read.');
 assert.equal(problemConsequence({...base,consequence:'Иной текст'}),'The available portion can still be read.');
 assert.equal(problemConsequence({...base,code:'processing_failed'}),base.consequence);
 const local={...base,code:'command_outcome_unknown' as const,saved:false,consequence:'Check the action status.'};
 assert.equal(problemConsequence(local),'Check the action status.');
});
