'use strict';
// Keep what was typed when the sign-in ends during a save (js/drafts.js).
const test = require('node:test');
const assert = require('node:assert/strict');
const { collect, keepable, decide, INTERRUPT_MS, OFFER_MS } = require('../drafts.js');

const field = (name, value, type = 'text', extra = {}) => ({ name, value, type, tagName: 'INPUT', ...extra });

test('collect: keeps typed fields, never passwords, files, hidden or tokens', () => {
    const fields = collect([
        field('name', 'Ghost'),
        field('csrfmiddlewaretoken', 'x', 'hidden'),
        field('form_token', 'y', 'hidden'),
        field('secret', 'pw', 'password'),
        field('photo', 'c:/a.jpg', 'file'),
        field('notes', 'Lame on the left fore', 'textarea'),
        field('primary', 'on', 'checkbox', { checked: true }),
        field('off', 'on', 'checkbox', { checked: false }),
    ]);
    assert.deepEqual(fields, [['name', 'Ghost'], ['notes', 'Lame on the left fore'], ['primary', 'on'], ['off', null]]);
});

test('keepable: never a sign-in, password or code form', () => {
    assert.equal(keepable([field('username', 'a')], '/accounts/login/'), false);
    assert.equal(keepable([field('code', '1')], '/accounts/two-step/'), false);
    assert.equal(keepable([field('name', 'a'), field('pw', 'x', 'password')], '/owners/1/edit/'), false);
    assert.equal(keepable([field('name', 'a')], '/owners/1/edit/'), true);
});

test('decide: the sign-in page marks only a fresh copy as interrupted', () => {
    const now = 1_000_000_000;
    assert.equal(decide({ time: now - 5000 }, now, true), 'interrupt');
    assert.equal(decide({ time: now - INTERRUPT_MS - 1 }, now, true), 'drop');
});

test('decide: after sign-in an interrupted copy is offered, for two hours', () => {
    const now = 1_000_000_000;
    assert.equal(decide({ time: now - 60_000, interrupted: true }, now, false), 'offer');
    assert.equal(decide({ time: now - OFFER_MS - 1, interrupted: true }, now, false), 'drop');
});

test('decide: a copy that was not interrupted means the save went through', () => {
    assert.equal(decide({ time: 1, interrupted: false }, 2, false), 'drop');
    assert.equal(decide(null, 2, false), 'drop');
});
