// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { nextIndex, placeFixed } = require('../more_menu.js');

test('nextIndex: Down from the button goes to the first item', () => {
    assert.equal(nextIndex(-1, 4, 'ArrowDown'), 0);
});

test('nextIndex: Up from the button goes to the last item', () => {
    assert.equal(nextIndex(-1, 4, 'ArrowUp'), 3);
});

test('nextIndex: Down and Up wrap round', () => {
    assert.equal(nextIndex(3, 4, 'ArrowDown'), 0);
    assert.equal(nextIndex(0, 4, 'ArrowUp'), 3);
    assert.equal(nextIndex(1, 4, 'ArrowDown'), 2);
});

test('nextIndex: Home and End jump to the ends', () => {
    assert.equal(nextIndex(2, 4, 'Home'), 0);
    assert.equal(nextIndex(1, 4, 'End'), 3);
});

test('nextIndex: an empty menu has nothing to focus', () => {
    assert.equal(nextIndex(-1, 0, 'ArrowDown'), -1);
});

test('nextIndex: other keys leave focus where it is', () => {
    assert.equal(nextIndex(2, 4, 'a'), 2);
});

// ── placeFixed: where a menu inside a clipping table goes ──

const button = (top, right) => ({ top, bottom: top + 32, right, left: right - 32 });

test('placeFixed: under the button, right edges lined up', () => {
    assert.deepEqual(placeFixed(button(100, 900), 200, 150, 1440, 900), { top: 138, left: 700 });
});

test('placeFixed: above the button when there is no room below', () => {
    assert.deepEqual(placeFixed(button(800, 900), 200, 150, 1440, 900), { top: 644, left: 700 });
});

test('placeFixed: stays below when there is no room above either', () => {
    assert.equal(placeFixed(button(60, 900), 200, 900, 1440, 900).top, 98);
});

test('placeFixed: never past the left or right edge of the window', () => {
    assert.equal(placeFixed(button(100, 120), 200, 150, 390, 844).left, 8);
    assert.equal(placeFixed(button(100, 400), 200, 150, 390, 844).left, 182);
});

test('placeFixed: a menu for a button at the start of a row opens rightward', () => {
    assert.equal(placeFixed(button(100, 120), 200, 150, 1440, 900, true).left, 88);
});
