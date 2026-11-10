import test from 'node:test';
import assert from 'node:assert/strict';
import { Draft } from '../../frontend/home-facts/draft.js';
const snapshot = (revision, answers) => ({ revision, answers, schema_version: 1, manifest: { template_sha256: 'test' } });

test('a delayed save acknowledgment preserves text typed while the request was running', () => {
  const draft = new Draft(snapshot(0, {}));
  draft.edit('roof', '2019'); const flight = draft.begin();
  draft.edit('roof', 'approximately 2019');
  assert.equal(draft.begin().mutation_id, flight.mutation_id);
  draft.acknowledge(snapshot(1, { roof: '2019' }));
  assert.equal(draft.answers.roof, 'approximately 2019');
  assert.equal(draft.begin().expected_revision, 1);
});

test('a reload retains the exact uncertain request and explicit clears', () => {
  const draft = new Draft(snapshot(2, { roof: '2019' }));
  draft.edit('roof', null); const flight = draft.begin();
  const restored = new Draft(snapshot(2, { roof: '2019' }), JSON.parse(JSON.stringify(draft.persisted())));
  assert.deepEqual(restored.begin(), flight);
  restored.acknowledge(snapshot(3, {}));
  assert.deepEqual(restored.pending, {});
});

test('another device update requires a visible choice and preserves unrelated local answers', () => {
  const draft = new Draft(snapshot(0, { roof: '2019' }));
  draft.edit('roof', '2020'); draft.edit('water', '2022');
  draft.reconcile(snapshot(1, { roof: '2021' }));
  assert.deepEqual(draft.conflicts.roof, { mine: '2020', theirs: '2021' });
  assert.equal(draft.begin(), null);
  draft.resolve('roof', false);
  assert.equal(draft.answers.roof, '2021');
  assert.deepEqual(draft.begin().changes, { water: '2022' });
});


test('reopening after the server saved an uncertain request preserves subsequent keystrokes without a false conflict', () => {
  const draft = new Draft(snapshot(0, {}));
  draft.edit('roof', '2019'); const flight = draft.begin();
  draft.edit('roof', 'approximately 2019');
  const restored = new Draft(snapshot(1, { roof: '2019' }), JSON.parse(JSON.stringify(draft.persisted())));
  assert.deepEqual(restored.conflicts, {});
  assert.equal(restored.begin().mutation_id, flight.mutation_id);
  restored.acknowledge(snapshot(1, { roof: '2019' }));
  assert.deepEqual(restored.begin().changes, { roof: 'approximately 2019' });
});


test('retrying a saved request cannot overwrite a newer answer from another device', () => {
  const draft = new Draft(snapshot(0, { roof: '2018' }));
  draft.edit('roof', '2019'); draft.begin();
  draft.edit('roof', 'approximately 2019');
  draft.acknowledge(snapshot(2, { roof: '2021' }));
  assert.deepEqual(draft.conflicts.roof, { mine: 'approximately 2019', theirs: '2021' });
  assert.equal(draft.begin(), null);
  draft.reconcile(snapshot(2, { roof: '2021' }));
  assert.equal(draft.begin(), null);
  const restored = new Draft(snapshot(2, { roof: '2021' }), JSON.parse(JSON.stringify(draft.persisted())));
  assert.equal(restored.begin(), null, 'Reload must retain the unresolved conflict');
  restored.resolve('roof', true);
  assert.equal(restored.begin().changes.roof, 'approximately 2019');
  draft.resolve('roof', false);
  assert.equal(draft.answers.roof, '2021');
});


test('a remote clear conflicts with a local answer when no matching request contains the field', () => {
  const draft = new Draft(snapshot(0, { roof: '2018' }));
  draft.edit('roof', '2020');
  draft.reconcile(snapshot(1, {}));
  assert.deepEqual(draft.conflicts.roof, { mine: '2020', theirs: null });
  assert.equal(draft.begin(), null);
});

test('relaunch retains a revert while the earlier save is still uncertain', () => {
  const draft = new Draft(snapshot(0, { roof: '2018' }));
  draft.edit('roof', '2019'); const sent = draft.begin();
  draft.edit('roof', '2018');
  const restored = new Draft(snapshot(0, { roof: '2018' }), JSON.parse(JSON.stringify(draft.persisted())));
  assert.equal(restored.begin().mutation_id, sent.mutation_id);
  restored.acknowledge(snapshot(1, { roof: '2019' }));
  assert.deepEqual(restored.begin().changes, { roof: '2018' });
});
