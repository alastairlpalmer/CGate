// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const {
    timeLimit, startedByPerson, failureMessage, safeToRefresh, awayLongEnough, AWAY_MS,
} = require('../connection.js');

// ── timeLimit: no request waits for ever ──

test('timeLimit: a page or a list gets 20 seconds', () => {
    assert.equal(timeLimit('get', false), 20000);
    assert.equal(timeLimit(undefined, false), 20000);
});

test('timeLimit: a save gets longer, because it can push to Xero', () => {
    assert.equal(timeLimit('post', false), 60000);
    assert.equal(timeLimit('DELETE', false), 60000);
});

test('timeLimit: an upload gets longest: a photo on a weak signal is slow, not dead', () => {
    assert.equal(timeLimit('post', true), 300000);
});

// ── startedByPerson: only a request a person asked for shows a message ──

test('startedByPerson: a tap, a form and a search box count', () => {
    assert.equal(startedByPerson('click', ''), true);
    assert.equal(startedByPerson('submit', null), true);
    assert.equal(startedByPerson('keyup', 'keyup changed delay:300ms'), true);
});

test('startedByPerson: a request made in code counts (a row tap opens a preview)', () => {
    assert.equal(startedByPerson('', ''), true);
});

test('startedByPerson: a card that loads by itself does not', () => {
    assert.equal(startedByPerson('', 'load'), false);
    assert.equal(startedByPerson('', 'every 60s'), false);
    assert.equal(startedByPerson('', 'revealed'), false);
});

test('startedByPerson: a count that refreshes on an app event does not', () => {
    assert.equal(startedByPerson('notif:changed', 'notif:changed from:body'), false);
});

// ── failureMessage: what to say, and if Try again is safe ──

test('failureMessage: a page that did not load can be asked for again', () => {
    assert.deepEqual(failureMessage('send', 'get', 0), { text: 'No signal. That did not load.', retry: true });
    assert.equal(failureMessage('timeout', 'get', 0).retry, true);
    assert.equal(failureMessage('response', 'get', 500).retry, true);
});

test('failureMessage: a save is never sent again by the message', () => {
    for (const kind of ['send', 'timeout', 'response']) {
        assert.equal(failureMessage(kind, 'post', 500).retry, false, kind);
    }
});

test('failureMessage: a save with no signal says the entries are still there', () => {
    assert.match(failureMessage('send', 'post', 0).text, /^Not saved: no signal\. Your entries are still here\./);
});

test('failureMessage: after a timeout, a save may have arrived: say so', () => {
    assert.match(failureMessage('timeout', 'post', 0).text, /may be saved\. Check before you save it again\.$/);
});

test('failureMessage: "no access" and "not found" do not offer Try again', () => {
    assert.equal(failureMessage('response', 'get', 403).retry, false);
    assert.equal(failureMessage('response', 'get', 404).retry, false);
    assert.match(failureMessage('response', 'post', 413).text, /too large/);
});

// ── safeToRefresh, awayLongEnough: the app comes back to the front ──

const QUIET = { offline: false, sheetOpen: false, overlayOpen: false, edited: false, rowsSelected: false, typing: false };

test('safeToRefresh: a page nobody is working on can be fetched again', () => {
    assert.equal(safeToRefresh(QUIET), true);
});

test('safeToRefresh: anything a refresh would lose stops it', () => {
    for (const key of Object.keys(QUIET)) {
        assert.equal(safeToRefresh({ ...QUIET, [key]: true }), false, key);
    }
});

test('awayLongEnough: five minutes in the background, not less', () => {
    assert.equal(awayLongEnough(1000, 1000 + AWAY_MS), true);
    assert.equal(awayLongEnough(1000, 1000 + AWAY_MS - 1), false);
});

test('awayLongEnough: a page that was never hidden is not "back"', () => {
    assert.equal(awayLongEnough(0, 10 * AWAY_MS), false);
});
