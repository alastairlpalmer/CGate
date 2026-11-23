// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { sectionPath, DIM_AFTER_MS } = require('../tap_feedback.js');

const HERE = 'https://yard.example/horses/?location=3';
const link = (attrs) => ({ getAttribute: (name) => (name in attrs ? attrs[name] : null) });

test('sectionPath: a tab gives the path it goes to', () => {
    assert.equal(sectionPath(link({ href: '/health/' }), HERE), '/health/');
    assert.equal(sectionPath(link({ href: '/horses/?status=departed' }), HERE), '/horses/');
});

test('sectionPath: a full address on this site counts', () => {
    assert.equal(sectionPath(link({ href: 'https://yard.example/finances/' }), HERE), '/finances/');
});

test('sectionPath: a link to another site does not change the section', () => {
    assert.equal(sectionPath(link({ href: 'https://example.org/help/' }), HERE), '');
});

test('sectionPath: a link that opens the pop-up sheet does not change the section', () => {
    // "Your profile" in the More sheet has an href and loads into the sheet.
    assert.equal(sectionPath(link({ href: '/accounts/profile/', 'hx-get': '/accounts/profile/' }), HERE), '');
});

test('sectionPath: no link, or a link to nowhere, gives nothing', () => {
    assert.equal(sectionPath(null, HERE), '');
    assert.equal(sectionPath(link({}), HERE), '');
    assert.equal(sectionPath(link({ href: '#' }), HERE), '');
});

test('a quick answer shows no dim: the wait is longer than a blink, shorter than a doubt', () => {
    assert.ok(DIM_AFTER_MS >= 100 && DIM_AFTER_MS <= 250);
});
