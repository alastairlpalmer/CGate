// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { prefixes } = require('../pedigree_tree.js');

test('prefixes: the horse itself is just the root', () => {
    assert.deepEqual(prefixes(''), ['']);
});

test("prefixes: a grandparent goes back through its parent to the horse", () => {
    assert.deepEqual(prefixes('DS'), ['', 'D', 'DS']);
});

test('prefixes: a third-generation box has four steps', () => {
    assert.deepEqual(prefixes('SDS'), ['', 'S', 'SD', 'SDS']);
});
