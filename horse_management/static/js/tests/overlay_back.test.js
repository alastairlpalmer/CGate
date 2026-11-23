// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { create } = require('../overlay_back.js');

// A browser history small enough to reason about. back() and forward()
// are queued, as in a browser: land() delivers the next one. A popstate
// that the overlay code does not take is recorded in `passed`: that is
// what htmx would get.
function setUp() {
    const env = {
        entries: [{ state: { htmx: true }, url: '/horses/' }],
        at: 0,
        moves: [],
        passed: [],
        timers: [],
        time: 0,
        closedByBack: [],
    };
    const history = {
        get state() { return env.entries[env.at].state; },
        pushState(state) {
            env.entries = env.entries.slice(0, env.at + 1);
            env.entries.push({ state, url: location.href });
            env.at += 1;
        },
        back() { env.moves.push(-1); },
        forward() { env.moves.push(1); },
    };
    const location = { href: '/horses/' };
    const overlay = create(history, location, (fn) => env.timers.push(fn), () => env.time);
    env.overlay = overlay;
    // Deliver every queued Back and Forward, in order.
    env.land = () => {
        while (env.moves.length) {
            env.at = Math.max(0, Math.min(env.entries.length - 1, env.at + env.moves.shift()));
            const state = env.entries[env.at].state;
            if (!overlay.onPop(state)) { env.passed.push(state); }
        }
    };
    // The user presses Back.
    env.pressBack = () => { history.back(); env.land(); };
    // The quiet-moment timer fires.
    env.tick = () => { const run = env.timers; env.timers = []; run.forEach((fn) => fn()); env.land(); };
    // htmx changes the address and puts new content in, in its own order:
    // save the page and mark the entry, push, swap, settle. `swap` is what
    // the new content does to the page (an overlay in the old content goes).
    env.navigate = (url, swap) => {
        overlay.pageWillBeSaved();
        env.entries[env.at] = { state: { htmx: true }, url: env.entries[env.at].url };
        const update = { type: 'push', path: url };
        overlay.historyWillUpdate(update);
        location.href = url;
        if (update.type === 'push') {
            history.pushState({ htmx: true });
        } else {
            env.entries[env.at] = { state: { htmx: true }, url };
        }
        overlay.historyUpdated();
        if (swap) { swap(); }
        overlay.pageSettled();
    };
    env.open = (name) => overlay.opened(name, () => env.closedByBack.push(name));
    env.urls = () => env.entries.map((entry) => entry.url + (entry.state.yardwayOverlay ? '#' + entry.state.yardwayOverlay : ''));
    return env;
}

test('opening an overlay adds one entry at the same address', () => {
    const env = setUp();
    env.open('notif');
    assert.deepEqual(env.urls(), ['/horses/', '/horses/#notif']);
    assert.equal(env.at, 1);
});

test('Back with an overlay open closes it and stays on the page', () => {
    const env = setUp();
    env.open('notif');
    env.pressBack();
    assert.deepEqual(env.closedByBack, ['notif']);
    assert.equal(env.at, 0);
    assert.deepEqual(env.passed, [], 'htmx must not see this Back');
});

test('a close from the X removes the entry, and htmx does not see that Back', () => {
    const env = setUp();
    env.open('notif');
    env.overlay.closed('notif');
    env.land();
    assert.equal(env.at, 0);
    assert.deepEqual(env.passed, []);
    assert.deepEqual(env.closedByBack, [], 'close is not called again');
});

test('after an overlay closed, the next Back is a normal one', () => {
    const env = setUp();
    env.navigate('/horses/14/');
    env.open('notif');
    env.overlay.closed('notif');
    env.land();
    env.pressBack();
    assert.equal(env.passed.length, 1, 'htmx gets the Back to the list');
    assert.equal(env.entries[env.at].url, '/horses/');
});

test('opening the same overlay twice adds one entry only', () => {
    const env = setUp();
    env.open('preview');
    env.open('preview');
    assert.equal(env.entries.length, 2);
});

test('an overlay that is open again after a reload does not add a second entry', () => {
    const env = setUp();
    env.open('preview');
    // A reload: the page and its scripts start again, the history stays.
    const fresh = create(
        { get state() { return env.entries[env.at].state; }, pushState() { throw new Error('no second entry'); }, back() {}, forward() {} },
        { href: '/horses/' }, () => {}, () => 0
    );
    fresh.opened('preview', () => {});
    assert.equal(fresh.isOpen('preview'), true);
});

test('two overlays: Back closes the top one first', () => {
    const env = setUp();
    env.open('preview');
    env.open('lightbox');
    env.pressBack();
    assert.deepEqual(env.closedByBack, ['lightbox']);
    env.pressBack();
    assert.deepEqual(env.closedByBack, ['lightbox', 'preview']);
    assert.equal(env.at, 0);
    assert.deepEqual(env.passed, []);
});

test('two overlays closed out of order leave no entry behind', () => {
    const env = setUp();
    env.open('preview');
    env.open('lightbox');
    env.overlay.closed('preview');   // its entry is under the other one
    env.land();
    assert.equal(env.at, 2, 'nothing is removed yet');
    env.overlay.closed('lightbox');
    env.land();
    assert.equal(env.at, 0, 'both entries are gone');
    assert.deepEqual(env.passed, []);
});

test('a Back that lands on the entry of an open overlay is not ours (the pop-up sheet closed above it)', () => {
    const env = setUp();
    env.open('preview');
    // popup.js adds its own entry on top, then removes it.
    env.entries.push({ state: { yardwayPopup: true }, url: '/horses/' });
    env.at = 2;
    env.pressBack();
    assert.deepEqual(env.closedByBack, [], 'the preview stays open');
    assert.equal(env.passed.length, 1, 'popup.js and htmx see the event');
});

test('rule 1: no entry is removed while a request is in flight', () => {
    const env = setUp();
    env.open('more');
    env.overlay.requestStarted();
    env.overlay.closed('more');
    env.land();
    assert.equal(env.at, 1, 'the entry is still the current one');
});

test('rule 2: a link in a drawer replaces the drawer entry with the new page', () => {
    const env = setUp();
    env.open('more');
    env.overlay.requestStarted();
    env.overlay.closed('more');
    env.overlay.requestEnded();
    env.navigate('/health/');
    env.tick();
    assert.deepEqual(env.urls(), ['/horses/', '/health/']);
    assert.equal(env.at, 1);
    env.pressBack();
    assert.equal(env.entries[env.at].url, '/horses/');
    assert.equal(env.passed.length, 1, 'one normal Back to the list');
});

test('rule 1: a request that fails leaves the entry, and the timer removes it', () => {
    const env = setUp();
    env.open('more');
    env.overlay.requestStarted();
    env.overlay.closed('more');
    env.overlay.requestEnded();      // no signal: the address does not change
    env.tick();
    assert.equal(env.at, 0);
    assert.deepEqual(env.passed, []);
});

test('rule 1: a request that never ends does not block for ever', () => {
    const env = setUp();
    env.open('more');
    env.overlay.requestStarted();
    env.time = 70000;                // longer than any request
    env.overlay.closed('more');
    env.land();
    assert.equal(env.at, 0);
});

test('rule 2: a link in the pop-up sheet replaces the sheet entry too', () => {
    const env = setUp();
    // popup.js adds its own entry; the helper only sees it in the history.
    env.entries.push({ state: { yardwayPopup: true }, url: '/horses/' });
    env.at = 1;
    env.navigate('/horses/14/edit/');
    assert.deepEqual(env.urls(), ['/horses/', '/horses/14/edit/']);
});

test('rule 2: a tap with nothing open adds an entry, as always', () => {
    const env = setUp();
    env.navigate('/health/');
    env.navigate('/horses/14/');
    assert.deepEqual(env.urls(), ['/horses/', '/health/', '/horses/14/']);
});

test('rule 3: an overlay that stays open through an address change gets a new entry', () => {
    const env = setUp();
    env.open('preview');
    env.navigate('/horses/?location=3');   // a filter on the list under it
    assert.deepEqual(env.urls(), ['/horses/', '/horses/?location=3', '/horses/?location=3#preview']);
    env.pressBack();
    assert.deepEqual(env.closedByBack, ['preview']);
    assert.equal(env.entries[env.at].url, '/horses/?location=3', 'the filter stays');
    assert.deepEqual(env.passed, []);
});

test('rule 4: the address changes while our Back is on its way', () => {
    const env = setUp();
    env.open('notif');
    env.overlay.closed('notif');     // history.back() is queued…
    env.navigate('/health/');        // …and the new page lands first
    env.land();
    assert.equal(env.entries[env.at].url, '/health/', 'we end on the new page');
    assert.equal(env.passed.length, 1, 'htmx restores the new page once');
});

test('an entry whose overlay is gone is stepped over', () => {
    const env = setUp();
    env.navigate('/horses/14/');
    // A full page load came back to an old overlay entry: nothing is open.
    env.entries.splice(1, 0, { state: { yardwayOverlay: 'notif' }, url: '/horses/' });
    env.at = 2;
    env.pressBack();
    assert.equal(env.at, 0, 'one press goes past the dead entry');
    assert.equal(env.passed.length, 1);
});

test('sync opens and closes by a flag', () => {
    const env = setUp();
    env.overlay.sync('more', true, () => {});
    assert.equal(env.overlay.isOpen('more'), true);
    env.overlay.sync('more', false, () => {});
    env.land();
    assert.equal(env.overlay.isOpen('more'), false);
    assert.equal(env.at, 0);
});

test('an overlay that a page change took away does not eat a Back', () => {
    const env = setUp();
    let open = true;
    env.overlay.opened('location', () => env.closedByBack.push('location'), () => open);
    // The overlay is in the old content: it is still there when the
    // address changes, and gone when the new content is in.
    env.navigate('/health/', () => { open = false; });
    assert.deepEqual(env.urls(), ['/horses/', '/health/'], 'its entry became the new page, with no new entry on top');
    env.pressBack();
    assert.deepEqual(env.closedByBack, [], 'nothing is "closed"');
    assert.equal(env.passed.length, 1, 'htmx gets the Back');
});
