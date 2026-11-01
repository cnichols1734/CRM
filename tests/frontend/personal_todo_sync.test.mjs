import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../static/js/todo-manager.js', import.meta.url), 'utf8');
function fixture(fetch = async () => {}) {
    const context = vm.createContext({ window: {}, console, fetch, crypto: globalThis.crypto });
    vm.runInContext(source, context);
    const manager = Object.create(context.window.TodoManager.prototype);
    Object.assign(manager, { config: {}, todos: ['One'], completedTodos: ['History'],
        identities: { active: ['1'], completed: ['2'] }, revision: 'r1', saveQueue: Promise.resolve() });
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
    const saveOne = manager.saveTodos();
    await Promise.resolve();
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
});
