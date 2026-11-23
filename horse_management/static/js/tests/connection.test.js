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

// ── What happens on phones: the events (item D7) ──

const { pathPattern, slowTap, track, SLOW_MS } = require('../connection.js');

test('pathPattern: a number in an address becomes :id', () => {
    assert.equal(pathPattern('/horses/54/'), '/horses/:id/');
    assert.equal(pathPattern('/horses/54/edit/'), '/horses/:id/edit/');
    assert.equal(pathPattern('/locations/3/stables/12/'), '/locations/:id/stables/:id/');
});

test('pathPattern: the query string is left off (the search box puts names in it)', () => {
    assert.equal(pathPattern('/owners/?q=smith'), '/owners/');
    assert.equal(pathPattern('/health/vaccinations/add/?horse=54'), '/health/vaccinations/add/');
    assert.equal(pathPattern('https://yard.example/horses/?needs=1#top'), '/horses/');
});

test('pathPattern: a key in a link becomes :key', () => {
    assert.equal(pathPattern('/accounts/reset/MQ/c8x1yz-6f0c5d9e2a7b4c1d/'), '/accounts/reset/MQ/:key/');
    assert.equal(pathPattern('/email/unsubscribe/abc123DEF456ghi789/'), '/email/unsubscribe/:key/');
    assert.equal(pathPattern('/f/AbCdEfGhIjKlMnOpQrSt/'), '/f/:key/');
});

test('pathPattern: plain words stay, also long ones', () => {
    assert.equal(pathPattern('/'), '/');
    assert.equal(pathPattern('/help/profile-and-security/'), '/help/profile-and-security/');
    assert.equal(pathPattern('/health/vaccination-types/add/'), '/health/vaccination-types/add/');
    assert.equal(pathPattern(undefined), '');
});

test('slowTap: 2 seconds or more counts, and the time is rounded', () => {
    assert.equal(SLOW_MS, 2000);
    assert.equal(slowTap(1999), null);
    assert.deepEqual(slowTap(2000), { seconds: 2 });
    assert.deepEqual(slowTap(2349), { seconds: 2.3 });
    assert.deepEqual(slowTap(15020), { seconds: 15 });
    assert.equal(slowTap(NaN), null);
});

test('track: nothing is sent when the usage figures are off or not loaded', () => {
    assert.equal(track(undefined, 'app slow tap', {}), false);
    assert.equal(track({ capture() {} }, 'app slow tap', {}), false);   // not loaded yet
});

test('track: one event with its properties, and a fault in it stays there', () => {
    const sent = [];
    const ph = { __loaded: true, capture: (name, props) => sent.push([name, props]) };
    assert.equal(track(ph, 'app request failed', { kind: 'send', status: 0 }), true);
    assert.deepEqual(sent, [['app request failed', { kind: 'send', status: 0 }]]);
    const broken = { __loaded: true, capture: () => { throw new Error('blocked'); } };
    assert.equal(track(broken, 'app slow tap'), false);
});
