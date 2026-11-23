// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { isInstalled, platform, cardPart } = require('../app_install.js');

const IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1';
const ANDROID = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36';
const MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15';

test('platform: an iPhone, an Android phone, a computer', () => {
    assert.equal(platform({ userAgent: IPHONE }), 'ios');
    assert.equal(platform({ userAgent: ANDROID }), 'android');
    assert.equal(platform({ userAgent: MAC, platform: 'MacIntel', maxTouchPoints: 0 }), 'other');
});

test('platform: an iPad says it is a Mac, and has a touch screen', () => {
    assert.equal(platform({ userAgent: MAC, platform: 'MacIntel', maxTouchPoints: 5 }), 'ios');
});

test('isInstalled: the app opened from its icon', () => {
    const win = (standalone, iosStandalone) => ({
        matchMedia: () => ({ matches: standalone }),
        navigator: { standalone: iosStandalone },
    });
    assert.equal(isInstalled(win(false, undefined)), false);
    assert.equal(isInstalled(win(true, undefined)), true);
    assert.equal(isInstalled(win(false, true)), true);   // an iPhone
    assert.equal(isInstalled({ navigator: {} }), false);
});

test('cardPart: Android gets the button when the browser offers to install', () => {
    assert.equal(cardPart({ touch: true, platform: 'android', canPrompt: true }), 'button');
});

test('cardPart: Android with no offer gets no card (the browser cannot install it now)', () => {
    assert.equal(cardPart({ touch: true, platform: 'android', canPrompt: false }), 'none');
});

test('cardPart: an iPhone gets the two steps', () => {
    assert.equal(cardPart({ touch: true, platform: 'ios', canPrompt: false }), 'steps');
});

test('cardPart: nothing in the installed app', () => {
    assert.equal(cardPart({ installed: true, touch: true, platform: 'ios' }), 'none');
    assert.equal(cardPart({ installed: true, touch: true, platform: 'android', canPrompt: true }), 'none');
});

test('cardPart: nothing after the person closed the card', () => {
    assert.equal(cardPart({ closed: true, touch: true, platform: 'ios' }), 'none');
    assert.equal(cardPart({ closed: true, touch: true, platform: 'android', canPrompt: true }), 'none');
});

test('cardPart: nothing on a computer', () => {
    assert.equal(cardPart({ touch: false, platform: 'other', canPrompt: true }), 'none');
});
