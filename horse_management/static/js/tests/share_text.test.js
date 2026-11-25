// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { plan, afterShareError } = require('../share_text.js');

const phone = { share: () => Promise.resolve() };

test('plan: a phone with a share sheet shares the text', () => {
    assert.equal(plan(phone, true, false), 'share');
});

test('plan: a computer copies, also when its browser can share', () => {
    assert.equal(plan(phone, false, false), 'copy');
});

test('plan: a phone with no share sheet copies', () => {
    assert.equal(plan({}, true, false), 'copy');
    assert.equal(plan(undefined, true, false), 'copy');
});

test('plan: "Copy the lots as text" always copies', () => {
    assert.equal(plan(phone, true, true), 'copy');
});

test('afterShareError: a closed share sheet is the end', () => {
    assert.equal(afterShareError({ name: 'AbortError' }), 'done');
});

test('afterShareError: any other fault copies the text, so it is not lost', () => {
    assert.equal(afterShareError({ name: 'NotAllowedError' }), 'copy');
    assert.equal(afterShareError(new Error('x')), 'copy');
    assert.equal(afterShareError(undefined), 'copy');
});
