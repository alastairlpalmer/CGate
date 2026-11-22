// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { isoDay, skip, allowed } = require('../date_quick.js');

function box(extra) {
    return Object.assign({ name: 'date', disabled: false, readOnly: false, dataset: {},
                           closest: () => null, form: null }, extra);
}

test('isoDay gives yesterday, today and tomorrow, across a month end', () => {
    const now = new Date(2026, 10, 30, 23, 30);  // 30 Nov, late evening
    assert.equal(isoDay(now, -1), '2026-11-29');
    assert.equal(isoDay(now, 0), '2026-11-30');
    assert.equal(isoDay(now, 1), '2026-12-01');
    assert.equal(isoDay(new Date(2027, 0, 1, 0, 5), -1), '2026-12-31');
});

test('skip leaves out filters, dates of birth, expiry and opted-out boxes', () => {
    assert.equal(skip(box()), false);
    assert.equal(skip(box({ name: 'foal_dob' })), false);
    assert.equal(skip(box({ name: 'date_of_birth' })), true);
    assert.equal(skip(box({ name: 'form-0-expiry_date' })), true);
    assert.equal(skip(box({ disabled: true })), true);
    assert.equal(skip(box({ readOnly: true })), true);
    assert.equal(skip(box({ dataset: { dateQuick: 'off' } })), true);
    assert.equal(skip(box({ dataset: { dateQuickDone: '1' } })), true);
    assert.equal(skip(box({ closest: () => ({}) })), true);
    assert.equal(skip(box({ form: { getAttribute: () => 'get' } })), true);
    assert.equal(skip(box({ form: { getAttribute: () => null } })), true);  // no method: GET
    assert.equal(skip(box({ form: { getAttribute: () => 'POST' } })), false);
});

test('allowed respects min and max', () => {
    assert.equal(allowed('2026-11-21', '', ''), true);
    assert.equal(allowed('2026-11-21', '2026-11-22', ''), false);
    assert.equal(allowed('2026-11-23', '', '2026-11-22'), false);
    assert.equal(allowed('2026-11-22', '2026-11-22', '2026-11-22'), true);
});
