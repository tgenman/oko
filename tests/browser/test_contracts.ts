import {test} from 'node:test';
import {validate} from './contracts.ts';
import assert from 'node:assert/strict';

test('fixture contracts reject missing ids, invalid counts and incomplete history', () => {
  assert.throws(() => validate('cards', [{title: 'missing id'}]), /id/);
  assert.throws(() => validate('navigation', {folders: [], feeds: [], count: '3'}), /count/);
  assert.throws(() => validate('status', {stage: 'Готово', errors: [], sources: [],
    ready_sources: 1, total_sources: 1, initial_histories: [{}]}), /initial_histories/);
});
