import {test} from 'node:test';
import {validate} from '../browser/contracts.ts';
import assert from 'node:assert/strict';

test('fixture contracts reject missing ids, invalid counts and incomplete history', () => {
  for (const name of ['cards', 'cards_v3']) {
    assert.throws(() => validate(name, {items:[{title:'missing id'}],total:1,next_cursor:null}), /id/);
    assert.doesNotThrow(() => validate(name, {items:[],total:0,next_cursor:null}));
  }
  for (const name of ['navigation', 'navigation_v3']) {
    assert.throws(() => validate(name, {folders: [], feeds: [], count: '3'}), /count/);
  }
  assert.throws(() => validate('navigation_subject', {folders: [], total: '3'}), /total/);
  assert.throws(() => validate('status_details', {stage: 'Готово', errors: [], sources: [],
    ready_sources: 1, total_sources: 1, initial_histories: [{}]}), /initial_histories/);
});
