// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { pickSection, parseFolds, withFold } = require('../settings_nav.js');

const ids = ['business', 'people', 'yard', 'billing', 'you', 'data'];

test('pickSection: the group named in the address wins', () => {
    assert.equal(pickSection(ids, '#billing', '', 'yard'), 'billing');
});

test('pickSection: an element inside a group opens that group', () => {
    assert.equal(pickSection(ids, '#services', 'billing', 'yard'), 'billing');
});

test('pickSection: with no address, the last group open in this tab', () => {
    assert.equal(pickSection(ids, '', '', 'yard'), 'yard');
});

test('pickSection: otherwise the first group', () => {
    assert.equal(pickSection(ids, '', '', ''), 'business');
});

test('pickSection: a group this user cannot see is ignored', () => {
    assert.equal(pickSection(['you'], '#billing', '', 'people'), 'you');
});

test('pickSection: no groups, no answer', () => {
    assert.equal(pickSection([], '#you', '', ''), '');
});

test('parseFolds: what the store holds becomes a record of folds', () => {
    assert.deepEqual(parseFolds('{"site:Home":true,"archived-locations":false}'), { 'site:Home': true, 'archived-locations': false });
    assert.deepEqual(parseFolds(null), {});
    assert.deepEqual(parseFolds(''), {});
});

test('parseFolds: a damaged store is an empty record, not an error', () => {
    assert.deepEqual(parseFolds('{not json'), {});
    assert.deepEqual(parseFolds('["site:Home"]'), {});
    assert.deepEqual(parseFolds('{"site:Home":true,"x":3,"y":null,"z":"open"}'), { 'site:Home': true });
});

test('withFold: a fold that opens or closes is kept, and the old record is not changed', () => {
    const before = { 'site:Home': true };
    assert.deepEqual(withFold(before, 'site:Away', true), { 'site:Home': true, 'site:Away': true });
    assert.deepEqual(withFold(before, 'site:Home', false), { 'site:Home': false });
    assert.deepEqual(before, { 'site:Home': true });
});

test('withFold: a closed fold is not the same as an untouched one', () => {
    // A site that opens by itself must stay closed after the reader closes it.
    const folds = withFold({}, 'locations:Main yard', false);
    assert.equal(Object.prototype.hasOwnProperty.call(folds, 'locations:Main yard'), true);
    assert.equal(folds['locations:Main yard'], false);
    assert.equal(Object.prototype.hasOwnProperty.call(folds, 'locations:Other'), false);
});
