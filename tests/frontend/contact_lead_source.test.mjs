import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const script = readFileSync(new URL('../../static/js/contact-lead-source.js', import.meta.url), 'utf8');

function setup(value, text = '') {
    const select = new EventTarget();
    select.value = value;
    const other = { value: text, disabled: false, required: false };
    const container = { hidden: false };
    const fields = {
        querySelector: (selector) => ({
            '[name="lead_source"]': select,
            '[name="lead_source_other"]': other,
            '[data-lead-source-other]': container,
        })[selector],
    };
    const document = new EventTarget();
    document.querySelectorAll = () => [fields];
    runInNewContext(script, { document });
    document.dispatchEvent(new Event('DOMContentLoaded'));
    return { select, other, container };
}

test('blank and standard sources omit the other field from submission', () => {
    for (const value of ['', 'Zillow', 'Instagram Organic']) {
        const { other, container } = setup(value);
        assert.equal(container.hidden, true);
        assert.equal(other.disabled, true);
        assert.equal(other.required, false);
    }
});

test('saved Other values are visible and editable on load', () => {
    const { other, container } = setup('Other', 'Local podcast');
    assert.equal(container.hidden, false);
    assert.equal(other.disabled, false);
    assert.equal(other.required, true);
    assert.equal(other.value, 'Local podcast');
});

test('switching choices updates visibility and keeps the unsaved draft', () => {
    const { select, other, container } = setup('Other', 'Draft source');
    select.value = 'Facebook Ad';
    select.dispatchEvent(new Event('change'));
    assert.equal(container.hidden, true);
    assert.equal(other.disabled, true);
    assert.equal(other.required, false);
    select.value = 'Other';
    select.dispatchEvent(new Event('change'));
    assert.equal(container.hidden, false);
    assert.equal(other.disabled, false);
    assert.equal(other.required, true);
    assert.equal(other.value, 'Draft source');
});
