import {test} from 'node:test';
import {validate} from '../browser/contracts.ts';
import assert from 'node:assert/strict';

test('fixture contracts reject missing ids, invalid counts and incomplete history', () => {
  for (const name of ['cards', 'cards_v3']) {
    assert.throws(() => validate(name, [{title: 'missing id'}]), /id/);
    assert.doesNotThrow(() => validate(name, []));
  }
  for (const name of ['navigation', 'navigation_v3']) {
    assert.throws(() => validate(name, {folders: [], feeds: [], count: '3'}), /count/);
  }
  assert.throws(() => validate('status', {stage: 'Готово', errors: [], sources: [],
    ready_sources: 1, total_sources: 1, initial_histories: [{}]}), /initial_histories/);
});
