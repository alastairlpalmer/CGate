// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { fitWithin, canShrink, jpegName, sizeLabel, MAX_SIDE } = require('../photo_quick_add.js');

// ── fitWithin: the size a photo is drawn at ──

test('fitWithin: a 12 megapixel phone photo comes down to 2,000 px on its long side', () => {
    assert.deepEqual(fitWithin(4032, 3024, MAX_SIDE), { width: 2000, height: 1500, scale: 2000 / 4032 });
});

test('fitWithin: a photo held upright keeps its shape', () => {
    const fit = fitWithin(3024, 4032, MAX_SIDE);
    assert.equal(fit.height, 2000);
    assert.equal(fit.width, 1500);
});

test('fitWithin: a photo that is small already is not made larger', () => {
    assert.deepEqual(fitWithin(1600, 1200, MAX_SIDE), { width: 1600, height: 1200, scale: 1 });
    assert.deepEqual(fitWithin(2000, 900, MAX_SIDE), { width: 2000, height: 900, scale: 1 });
});

test('fitWithin: a photo with no size (it did not load) is left alone', () => {
    assert.equal(fitWithin(0, 0, MAX_SIDE).scale, 1);
});

// ── canShrink: which files are changed ──

test('canShrink: camera photos are', () => {
    assert.equal(canShrink('image/jpeg'), true);
    assert.equal(canShrink('image/webp'), true);
});

test('canShrink: a PNG is not: it can have clear parts, and is most often a screenshot', () => {
    assert.equal(canShrink('image/png'), false);
});

test('canShrink: a file the browser may not be able to read goes as it is', () => {
    assert.equal(canShrink('image/heic'), false);
    assert.equal(canShrink('image/gif'), false);
    assert.equal(canShrink(''), false);
});

// ── jpegName: the file is a JPEG after the change ──

test('jpegName: the ending follows the new format', () => {
    assert.equal(jpegName('IMG_4021.HEIC'), 'IMG_4021.jpg');
    assert.equal(jpegName('thunder near fore.webp'), 'thunder near fore.jpg');
    assert.equal(jpegName('IMG_4021.jpeg'), 'IMG_4021.jpg');
});

test('jpegName: a name with no ending, or no name, still gives a file name', () => {
    assert.equal(jpegName('photo'), 'photo.jpg');
    assert.equal(jpegName(''), 'photo.jpg');
    assert.equal(jpegName('.jpg'), 'photo.jpg');
});

// ── sizeLabel ──

test('sizeLabel: kilobytes under one megabyte, megabytes above', () => {
    assert.equal(sizeLabel(480 * 1024), '480 KB');
    assert.equal(sizeLabel(3.2 * 1024 * 1024), '3.2 MB');
    assert.equal(sizeLabel(200), '1 KB');
});
