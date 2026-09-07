// Run with: node --test static/js/tests   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const picker = require('../service_picker.js');

test('decide: nothing chosen leaves the form alone', () => {
    assert.deepEqual(picker.decide(null, '12.00'), { mode: 'none' });
    assert.deepEqual(picker.decide({ value: '' }, ''), { mode: 'none' });
});

test('decide: Other unlocks everything', () => {
    assert.deepEqual(picker.decide({ value: 'other', price: null }, '5'), { mode: 'other' });
});

test('decide: a preset locks its price and carries its fill values', () => {
    const state = picker.decide(
        { value: '3', price: '17.00', fill: { product_name: 'Wormer - Equimax' } },
        ''
    );
    assert.equal(state.mode, 'preset');
    assert.equal(state.price, '17.00');
    assert.equal(state.overridden, false);
    assert.deepEqual(state.fill, { product_name: 'Wormer - Equimax' });
});

test('decide: a cost equal to the price is not an override', () => {
    assert.equal(picker.decide({ value: '3', price: '17.00' }, '17').overridden, false);
    assert.equal(picker.decide({ value: '3', price: '17.00' }, ' 17.0 ').overridden, false);
});

test('decide: a different cost after a re-render keeps the override', () => {
    const state = picker.decide({ value: '3', price: '17.00' }, '15.50');
    assert.equal(state.overridden, true);
    assert.equal(state.price, '17.00');
});

test('formatPrice', () => {
    assert.equal(picker.formatPrice('17'), '£17.00');
    assert.equal(picker.formatPrice('21.5'), '£21.50');
    assert.equal(picker.formatPrice('abc'), '£abc');
});
