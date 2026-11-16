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

test('parseFolds: what the store holds becomes a list of keys', () => {
    assert.deepEqual(parseFolds('["site:Home","archived-locations"]'), ['site:Home', 'archived-locations']);
    assert.deepEqual(parseFolds(null), []);
    assert.deepEqual(parseFolds(''), []);
});

test('parseFolds: a damaged store is an empty list, not an error', () => {
    assert.deepEqual(parseFolds('{not json'), []);
    assert.deepEqual(parseFolds('{"a":1}'), []);
    assert.deepEqual(parseFolds('["site:Home", 3, null]'), ['site:Home']);
});

test('withFold: a fold that opens is added once', () => {
    assert.deepEqual(withFold([], 'site:Home', true), ['site:Home']);
    assert.deepEqual(withFold(['site:Home'], 'site:Home', true), ['site:Home']);
    assert.deepEqual(withFold(['site:Home'], 'site:Away', true), ['site:Home', 'site:Away']);
});

test('withFold: a fold that closes is taken out', () => {
    assert.deepEqual(withFold(['site:Home', 'site:Away'], 'site:Home', false), ['site:Away']);
    assert.deepEqual(withFold([], 'site:Home', false), []);
});
