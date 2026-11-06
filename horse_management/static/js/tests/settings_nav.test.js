// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { pickSection } = require('../settings_nav.js');

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
