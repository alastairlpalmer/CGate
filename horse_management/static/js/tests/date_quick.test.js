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

// ── A date that is weeks or months away (C8) ──

const { parseSpec, shift } = require('../date_quick.js');

test('parseSpec reads "+6m +12m" as two buttons with their words', () => {
    assert.deepEqual(parseSpec('+6m +12m'), [
        { amount: 6, unit: 'm', label: '+6 months' },
        { amount: 12, unit: 'm', label: '+12 months' },
    ]);
    assert.deepEqual(parseSpec('+6w,+8w').map((c) => c.label), ['+6 weeks', '+8 weeks']);
    assert.deepEqual(parseSpec('+1w +1d +1m').map((c) => c.label), ['+1 week', '+1 day', '+1 month']);
});

test('parseSpec gives nothing for "off", for no value and for other words', () => {
    assert.deepEqual(parseSpec('off'), []);
    assert.deepEqual(parseSpec(undefined), []);
    assert.deepEqual(parseSpec('6m -2w +3y'), []);
});

test('shift adds months, and a short month gives its last day', () => {
    assert.equal(shift('2026-11-23', 6, 'm'), '2027-05-23');
    assert.equal(shift('2026-11-23', 12, 'm'), '2027-11-23');
    assert.equal(shift('2026-08-31', 6, 'm'), '2027-02-28');
    assert.equal(shift('2024-02-29', 12, 'm'), '2025-02-28');
    assert.equal(shift('2027-08-31', 6, 'm'), '2028-02-29');
});

test('shift adds weeks and days across a month and a year', () => {
    assert.equal(shift('2026-11-23', 6, 'w'), '2027-01-04');
    assert.equal(shift('2026-11-23', 8, 'w'), '2027-01-18');
    assert.equal(shift('2026-12-31', 2, 'd'), '2027-01-02');
});

test('a box with its own buttons is not skipped', () => {
    assert.equal(skip(box({ dataset: { dateQuick: '+6m +12m', dateQuickFrom: 'date_given' } })), false);
});
