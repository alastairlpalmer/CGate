// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { stepIndex, snapFor, translateYOf } = require('../split_view.js');

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

// ── translateYOf: where the sheet actually is ──
// A drag starts from the sheet's real position. Reading its class instead
// is a guess, and the guess is wrong for a finger that lands mid-settle.

test('translateYOf: no transform is no offset', () => {
    assert.equal(translateYOf('none'), 0);
    assert.equal(translateYOf(''), 0);
    assert.equal(translateYOf(undefined), 0);
});

test('translateYOf: reads the offset out of a 2D matrix', () => {
    // matrix(a, b, c, d, tx, ty) — ty is the sixth.
    assert.equal(translateYOf('matrix(1, 0, 0, 1, 0, 278)'), 278);
    assert.equal(translateYOf('matrix(1, 0, 0, 1, 0, 0)'), 0);
});

test('translateYOf: reads it out of a 3D matrix too', () => {
    // A promoted layer reports matrix3d; ty is the fourteenth of sixteen.
    const m = 'matrix3d(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 278, 0, 1)';
    assert.equal(translateYOf(m), 278);
});

test('translateYOf: a fractional offset survives', () => {
    // The settle animation is mid-flight most of the time it is read.
    assert.equal(translateYOf('matrix(1, 0, 0, 1, 0, 143.5)'), 143.5);
});

test('translateYOf: anything it cannot read is no offset, not NaN', () => {
    // NaN would poison the drag maths and freeze the sheet.
    assert.equal(translateYOf('rotate(3deg)'), 0);
    assert.equal(translateYOf('matrix(nonsense)'), 0);
});

// ── snapFor, with the real peek height ──
// The peek was being read as 34 (from the text "34dvh") instead of the
// ~290 px it lays out to, so these thresholds all sat in the wrong place.

test('snapFor: a pull to halfway between peek and full opens the sheet', () => {
    // 852px phone: peek 290, full 640. Midpoint 465.
    assert.equal(snapFor(470, 290, 640, 64), 'full');
    assert.equal(snapFor(460, 290, 640, 64), 'peek');
});

test('snapFor: a firm pull down puts the sheet away', () => {
    // Under 60% of the peek height — 174 px of 290.
    assert.equal(snapFor(170, 290, 640, 64), 'closed');
    assert.equal(snapFor(200, 290, 640, 64), 'peek');
});

test('snapFor: pulling past the sheet asks for the whole page', () => {
    assert.equal(snapFor(704, 290, 640, 64), 'page');
    assert.equal(snapFor(703, 290, 640, 64), 'full');
});
