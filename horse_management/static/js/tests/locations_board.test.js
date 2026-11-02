// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { matchesQuery, compareRows, restSummary } = require('../locations_board.js');

// ── matchesQuery: the rail's filter box ──

test('matchesQuery: an empty box keeps everything', () => {
    assert.ok(matchesQuery('hawkhill', ''));
    assert.ok(matchesQuery('', ''));
});

test('matchesQuery: matches anywhere in the name, not just the start', () => {
    // "Jones's mid west" should be findable by typing "west".
    assert.ok(matchesQuery("jones's mid west", 'west'));
    assert.ok(matchesQuery('hill-whitakers', 'whit'));
});

test('matchesQuery: a name that does not contain the text is dropped', () => {
    assert.ok(!matchesQuery('hawkhill', 'barn'));
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

test('compareRows: no rest record sorts below one that has rested nought days', () => {
    const rows = [row('Ash', 0, -1), row('Beech', 0, 0)];
    rows.sort((a, b) => compareRows('rest', a, b));
    assert.deepEqual(rows.map(r => r.name), ['Beech', 'Ash']);
});

// ── restSummary: the line beside "Rest & rotation" ──

test('restSummary: counts each state it has something to say about', () => {
    const states = ['rested', 'rested', 'recovering', 'grazing', 'over'];
    assert.equal(restSummary(states), '2 ready · 1 recovering · 1 over');
});

test('restSummary: says nothing about a state with nothing in it', () => {
    assert.equal(restSummary(['rested', 'rested']), '2 ready');
    assert.equal(restSummary(['grazing', 'grazing']), '');
});

test('restSummary: an empty site has no summary at all', () => {
    assert.equal(restSummary([]), '');
});

test('restSummary: empty ground is not counted as ready', () => {
    // "empty" is grazing ground nobody has marked rested; claiming it is
    // ready would send horses onto ground that has not had its break.
    assert.equal(restSummary(['empty', 'empty', 'rested']), '1 ready');
});
