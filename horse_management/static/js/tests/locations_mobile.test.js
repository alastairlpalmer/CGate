// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const {
    SNAPS, nextSnap, snapFromHeight, rowPasses, compareRows,
} = require('../locations_mobile.js');

// ── nextSnap: tapping the handle ──

test('nextSnap: the handle walks peek → half → full and round again', () => {
    assert.deepEqual(SNAPS, ['peek', 'half', 'full']);
    assert.equal(nextSnap('peek'), 'half');
    assert.equal(nextSnap('half'), 'full');
    assert.equal(nextSnap('full'), 'peek');
});

// ── snapFromHeight: where a drag lands ──

const HEIGHTS = { peek: 170, half: 420, full: 660 };

test('snapFromHeight: a drag settles on the nearest height', () => {
    assert.equal(snapFromHeight(180, HEIGHTS), 'peek');
    assert.equal(snapFromHeight(400, HEIGHTS), 'half');
    assert.equal(snapFromHeight(640, HEIGHTS), 'full');
});

test('snapFromHeight: halfway between two heights picks the lower one', () => {
    // (170 + 420) / 2 = 295 — the tie goes to peek, which is the height
    // that keeps the map on screen.
    assert.equal(snapFromHeight(295, HEIGHTS), 'peek');
});

test('snapFromHeight: an over-drag past the top is still just full', () => {
    // Unlike the horse sheet, there is nowhere further to go: the list is
    // the other half of this page, so a hard pull must not throw it away.
    assert.equal(snapFromHeight(900, HEIGHTS), 'full');
    assert.equal(snapFromHeight(0, HEIGHTS), 'peek');
});

// ── rowPasses: the All / Occupied / Rested filter ──

test('rowPasses: All keeps everything', () => {
    assert.ok(rowPasses('all', 'rested', 0));
    assert.ok(rowPasses('all', 'over', 14));
});

test('rowPasses: Occupied is about horses, not about land use', () => {
    assert.ok(rowPasses('occupied', 'grazing', 3));
    assert.ok(rowPasses('occupied', 'over', 14));
    assert.ok(!rowPasses('occupied', 'rested', 0));
});

test('rowPasses: Rested means rested long enough, not merely empty', () => {
    assert.ok(rowPasses('rested', 'rested', 0));
    assert.ok(!rowPasses('rested', 'recovering', 0));
    assert.ok(!rowPasses('rested', 'empty', 0));
});

// ── compareRows: the two sorts ──

const row = (name, count, rest) => ({ name, count, rest });

test('compareRows: most horses first', () => {
    const rows = [row('Beech', 2, -1), row('Ash', 9, -1), row('Cedar', 5, -1)];
    rows.sort((a, b) => compareRows('horses', a, b));
    assert.deepEqual(rows.map(r => r.name), ['Ash', 'Cedar', 'Beech']);
});

test('compareRows: longest rested first', () => {
    const rows = [row('Beech', 0, 4), row('Ash', 0, 41), row('Cedar', 0, 12)];
    rows.sort((a, b) => compareRows('rest', a, b));
    assert.deepEqual(rows.map(r => r.name), ['Ash', 'Cedar', 'Beech']);
});

test('compareRows: a tie falls back to the name, so the order is stable', () => {
    const rows = [row('Willow', 3, -1), row('Ash', 3, -1), row('Maple', 3, -1)];
    rows.sort((a, b) => compareRows('horses', a, b));
    assert.deepEqual(rows.map(r => r.name), ['Ash', 'Maple', 'Willow']);
});

test('compareRows: a location with no rest record sorts below one with', () => {
    // "not rested" is stored as -1, which must not read as "rested least".
    const rows = [row('Ash', 0, -1), row('Beech', 0, 0)];
    rows.sort((a, b) => compareRows('rest', a, b));
    assert.deepEqual(rows.map(r => r.name), ['Beech', 'Ash']);
});
