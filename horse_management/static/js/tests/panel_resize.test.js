// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { clampPanelWidth, dragWidth } = require('../panel_resize.js');

// ── clampPanelWidth: the limits on a resized panel ──

test('clampPanelWidth: a width inside the limits is kept', () => {
    assert.equal(clampPanelWidth(500, 1400, 336, 448), 500);
});

test('clampPanelWidth: the panel never goes below its minimum', () => {
    assert.equal(clampPanelWidth(120, 1400, 336, 448), 336);
});

test('clampPanelWidth: the other side always keeps its minimum', () => {
    assert.equal(clampPanelWidth(1300, 1400, 336, 448), 952);
});

test('clampPanelWidth: a host too narrow for both keeps the panel minimum', () => {
    assert.equal(clampPanelWidth(500, 700, 336, 448), 336);
});

test('clampPanelWidth: rounds to whole pixels and treats junk as the minimum', () => {
    assert.equal(clampPanelWidth(500.6, 1400, 336, 448), 501);
    assert.equal(clampPanelWidth('nope', 1400, 336, 448), 336);
});

// ── dragWidth: which way the bar moves the panel ──

test('dragWidth: a panel on the right grows when the bar moves left', () => {
    assert.equal(dragWidth(400, -50, 'right'), 450);
    assert.equal(dragWidth(400, 50, 'right'), 350);
});

test('dragWidth: a panel on the left grows when the bar moves right', () => {
    assert.equal(dragWidth(400, 50, 'left'), 450);
    assert.equal(dragWidth(400, -50, 'left'), 350);
});
