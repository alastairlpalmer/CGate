// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { stepFor } = require('../horse_step.js');

function key(k, extra) {
    return Object.assign({ key: k, target: { closest: () => null } }, extra || {});
}

test('stepFor: left is previous and right is next', () => {
    assert.equal(stepFor(key('ArrowLeft')), 'prev');
    assert.equal(stepFor(key('ArrowRight')), 'next');
});

test('stepFor: other keys do nothing', () => {
    assert.equal(stepFor(key('ArrowDown')), null);
    assert.equal(stepFor(key('j')), null);
});

test('stepFor: a modifier keeps the browser shortcut (Alt + left is Back)', () => {
    assert.equal(stepFor(key('ArrowLeft', { altKey: true })), null);
    assert.equal(stepFor(key('ArrowRight', { metaKey: true })), null);
    assert.equal(stepFor(key('ArrowRight', { shiftKey: true })), null);
});

test('stepFor: not while typing, in a menu or in the pedigree diagram', () => {
    const inside = { closest: () => ({}) };
    assert.equal(stepFor(key('ArrowLeft', { target: inside })), null);
});

test('stepFor: a key another handler took is left alone', () => {
    assert.equal(stepFor(key('ArrowLeft', { defaultPrevented: true })), null);
});
