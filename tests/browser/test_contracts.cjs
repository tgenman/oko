const {test} = require('node:test');
const assert = require('node:assert/strict');
const {reader, material} = require('./support.cjs');

test('browser snapshot contract rejects a missing publication id, wrong navigation count and incomplete history', async () => {
  const r = await reader();
  try {
    await r.start([material('one')]);
    const result = await r.page.evaluate(async () => {
      const badCard = {...readerTransport.replies.get('/api/cards').body[0]};
      delete badCard.id;
      const errors = [];
      for (const [url,body] of [['/api/cards',[badCard]], ['/api/navigation',{folders:[],feeds:[],count:'3'}], ['/api/status',{...readerTransport.replies.get('/api/status').body,initial_histories:[{}]}]]) {
        try {await window.validateReaderReply(url,'GET',200,body);}
        catch (error) {errors.push(error.message);}
      }
      return errors;
    });
    assert.equal(result.length, 3);
    assert.match(result[0], /id/);
    assert.match(result[1], /count/);
    assert.match(result[2], /initial_histories/);
  } finally {await r.close();}
});
