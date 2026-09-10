// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { stepIndex, snapFor } = require('../split_view.js');

// ── stepIndex: arrow keys walking the list ──

test('stepIndex: nothing selected yet, down picks the first row', () => {
    assert.equal(stepIndex(-1, 12, 1), 0);
});

test('stepIndex: nothing selected yet, up picks the last row', () => {
    assert.equal(stepIndex(-1, 12, -1), 11);
});

test('stepIndex: steps one row at a time', () => {
    assert.equal(stepIndex(0, 12, 1), 1);
    assert.equal(stepIndex(5, 12, -1), 4);
});

test('stepIndex: stops at both ends rather than wrapping round', () => {
    // Wrapping would send someone at the bottom of a long list back to the
    // top with no warning, which reads as a lost place.
    assert.equal(stepIndex(11, 12, 1), 11);
    assert.equal(stepIndex(0, 12, -1), 0);
});

test('stepIndex: an empty list has nothing to select', () => {
    assert.equal(stepIndex(-1, 0, 1), -1);
    assert.equal(stepIndex(3, 0, -1), -1);
});

// ── snapFor: where a dragged sheet lands ──

const PEEK = 240;
const FULL = 700;
const OVERSHOOT = 64;

test('snapFor: a small pull from peek settles back at peek', () => {
    assert.equal(snapFor(300, PEEK, FULL, OVERSHOOT), 'peek');
});

test('snapFor: past the midpoint the sheet opens fully', () => {
    assert.equal(snapFor((PEEK + FULL) / 2, PEEK, FULL, OVERSHOOT), 'full');
    assert.equal(snapFor(FULL, PEEK, FULL, OVERSHOOT), 'full');
});

test('snapFor: dragging well below peek puts the sheet away', () => {
    assert.equal(snapFor(100, PEEK, FULL, OVERSHOOT), 'closed');
    assert.equal(snapFor(0, PEEK, FULL, OVERSHOOT), 'closed');
});

test('snapFor: dragging past the top asks for the full page', () => {
    assert.equal(snapFor(FULL + OVERSHOOT, PEEK, FULL, OVERSHOOT), 'page');
    assert.equal(snapFor(FULL + 200, PEEK, FULL, OVERSHOOT), 'page');
});

test('snapFor: just short of the overshoot is still only full', () => {
    assert.equal(snapFor(FULL + OVERSHOOT - 1, PEEK, FULL, OVERSHOOT), 'full');
});

test('snapFor: the overshoot has a default when none is given', () => {
    assert.equal(snapFor(FULL + 64, PEEK, FULL), 'page');
    assert.equal(snapFor(FULL + 10, PEEK, FULL), 'full');
});
