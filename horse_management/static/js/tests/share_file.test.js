// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { canShare, afterShareError, isPdf } = require('../share_file.js');

class FakeFile { constructor(parts, name, options) { this.name = name; this.type = options.type; } }
const nav = (answer) => ({ share: () => Promise.resolve(), canShare: (data) => (typeof answer === 'function' ? answer(data) : answer) });

test('canShare: a phone that can share a PDF', () => {
    let asked = null;
    const phone = nav((data) => { asked = data; return true; });
    assert.equal(canShare(phone, true, FakeFile), true);
    assert.equal(asked.files[0].type, 'application/pdf');
});

test('canShare: a computer downloads, also when its browser can share', () => {
    assert.equal(canShare(nav(true), false, FakeFile), false);
});

test('canShare: a phone that cannot share a file follows the link', () => {
    assert.equal(canShare(nav(false), true, FakeFile), false);
    assert.equal(canShare({ share: () => {} }, true, FakeFile), false);      // no canShare: an old browser
    assert.equal(canShare({}, true, FakeFile), false);
    assert.equal(canShare(undefined, true, FakeFile), false);
});

test('canShare: a browser that throws on the question cannot', () => {
    assert.equal(canShare(nav(() => { throw new TypeError('files'); }), true, FakeFile), false);
});

test('afterShareError: the person closed the sheet', () => {
    assert.equal(afterShareError({ name: 'AbortError' }), 'done');
});

test('afterShareError: the download took too long for the first tap', () => {
    assert.equal(afterShareError({ name: 'NotAllowedError' }), 'ask');
});

test('afterShareError: anything else opens the file in the old way', () => {
    assert.equal(afterShareError({ name: 'DataError' }), 'open');
    assert.equal(afterShareError(new TypeError('x')), 'open');
    assert.equal(afterShareError(undefined), 'open');
});

const response = (ok, redirected, type) => ({ ok, redirected, headers: { get: () => type } });

test('isPdf: the PDF', () => {
    assert.equal(isPdf(response(true, false, 'application/pdf')), true);
    assert.equal(isPdf(response(true, false, 'Application/PDF; charset=binary')), true);
});

test('isPdf: the sign-in page, an error page and a file of another kind are not', () => {
    assert.equal(isPdf(response(true, true, 'text/html; charset=utf-8')), false);   // sent to sign in
    assert.equal(isPdf(response(true, false, 'text/html')), false);
    assert.equal(isPdf(response(false, false, 'application/pdf')), false);
    assert.equal(isPdf(response(true, true, 'application/pdf')), false);
    assert.equal(isPdf(response(true, false, null)), false);
});
