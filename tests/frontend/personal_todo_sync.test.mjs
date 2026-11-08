import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../static/js/todo-manager.js', import.meta.url), 'utf8');
function fixture(fetch = async () => {}) {
    const context = vm.createContext({ window: { alert() {} }, console, fetch, crypto: globalThis.crypto });
    vm.runInContext(source, context);
    const manager = Object.create(context.window.TodoManager.prototype);
    Object.assign(manager, { config: {}, todos: ['One'], completedTodos: ['History'],
        subtodos: {}, identities: { active: ['1'], completed: ['2'] }, revision: 'r1', saveQueue: Promise.resolve() });
    return manager;
}

test('dashboard DOM reorder retains hidden completed todos and their identities', () => {
    const manager = fixture();
    const node = (text, id) => ({ textContent: text, closest: selector => selector === '.todo-item' ? { dataset: { todoId: id } } : null });
    manager.activeList = { querySelectorAll: () => [node('Same title', '4'), node('Same title', '3')] };
    manager.completedList = null;
    manager.syncFromDOM();
    assert.deepEqual(Array.from(manager.identities.active), ['4', '3']);
    assert.deepEqual(manager.completedTodos, ['History']);
    assert.deepEqual(manager.identities.completed, ['2']);
});

test('queued edits retain row identities and use the prior save revision', async () => {
    const sent = [];
    let release;
    const first = new Promise(resolve => { release = resolve; });
    const manager = fixture(async (_url, request) => {
        sent.push(JSON.parse(request.body));
        const number = sent.length;
        if (number === 1) await first;
        return { ok: true, headers: { get: () => `r${number + 1}` } };
    });
    manager.todos = ['Renamed'];
    manager.subtodos = { '1': [{ id: 'child', text: 'First edit', completed: false }] };
    const saveOne = manager.saveTodos();
    await Promise.resolve();
    manager.subtodos['1'][0].text = 'Second edit';
    manager.todos = [];
    manager.completedTodos = ['History', 'Renamed'];
    manager.identities = { active: [], completed: ['2', '1'] };
    const saveTwo = manager.saveTodos();
    assert.equal(sent.length, 1);
    release();
    await Promise.all([saveOne, saveTwo]);
    assert.deepEqual(sent[0].identities, { active: ['1'], completed: ['2'] });
    assert.deepEqual(sent[1].identities, { active: [], completed: ['2', '1'] });
    assert.equal(sent[1].revision, 'r2');
    assert.equal(sent[0].subtodos['1'][0].text, 'First edit');
    assert.equal(sent[1].subtodos['1'][0].text, 'Second edit');
});

test('a conflict reloads sub-to-dos and cancels queued stale edits', async () => {
    let posts = 0;
    const manager = fixture(async (_url, request) => {
        if (request) { posts += 1; return { ok: false, status: 409 }; }
        return { ok: true, headers: { get: () => 'fresh' }, json: async () => ({
            active: ['One'], completed: [], identities: { active: ['1'], completed: [] },
            subtodos: { '1': [{ id: 'child', text: 'Phone edit', completed: true }] }
        }) };
    });
    manager.render = manager.updateCounts = manager.updateEmptyStates = () => {};
    manager.subtodos = { '1': [{ id: 'child', text: 'Stale edit', completed: false }] };
    const first = manager.saveTodos();
    const second = manager.saveTodos();
    await Promise.all([first, second]);
    assert.equal(posts, 1);
    assert.equal(manager.subtodos['1'][0].text, 'Phone edit');
    assert.equal(manager.revision, 'fresh');
});
