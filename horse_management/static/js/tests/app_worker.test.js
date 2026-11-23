// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const { plan, cacheName, PREFIX } = require('../app_worker.js');

const ORIGIN = 'https://yard.example';
const CONFIG = {
    version: 'abc12345',
    offline: '/offline/',
    needed: ['/offline/', '/static/css/styles.css?v=abc12345', '/static/fonts/DMSans-Variable.woff2', '/static/icons/icon-192.png'],
    wanted: ['/static/js/htmx.min.js?v=abc12345'],
    staticPrefix: '/static/',
    skip: ['/c-vet/'],
};
const get = (path, mode) => ({ method: 'GET', url: ORIGIN + path, mode: mode || 'navigate' });

test('a page load goes to the network, with the "No signal" page behind it', () => {
    assert.equal(plan(get('/'), CONFIG, ORIGIN), 'page');
    assert.equal(plan(get('/horses/54/'), CONFIG, ORIGIN), 'page');
    assert.equal(plan(get('/owners/?q=smith'), CONFIG, ORIGIN), 'page');
    assert.equal(plan(get('/accounts/login/'), CONFIG, ORIGIN), 'page');
});

test('a tap inside the app is left to the browser: htmx asks, and the page says "No signal"', () => {
    assert.equal(plan(get('/horses/', 'cors'), CONFIG, ORIGIN), 'network');
    assert.equal(plan(get('/notifications/badge/', 'same-origin'), CONFIG, ORIGIN), 'network');
});

test('a save is never touched', () => {
    for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
        assert.equal(plan({ method, url: ORIGIN + '/horses/54/move/', mode: 'navigate' }, CONFIG, ORIGIN), 'network');
        assert.equal(plan({ method, url: ORIGIN + '/horses/54/move/', mode: 'cors' }, CONFIG, ORIGIN), 'network');
    }
});

test('a file of this version is kept on the phone', () => {
    assert.equal(plan(get('/static/js/connection.js?v=abc12345', 'no-cors'), CONFIG, ORIGIN), 'kept');
    assert.equal(plan(get('/static/css/styles.css?v=abc12345', 'no-cors'), CONFIG, ORIGIN), 'kept');
});

test('a font or an icon the "No signal" page needs is kept, with no version in its address', () => {
    assert.equal(plan(get('/static/fonts/DMSans-Variable.woff2', 'cors'), CONFIG, ORIGIN), 'kept');
    assert.equal(plan(get('/static/icons/icon-192.png', 'no-cors'), CONFIG, ORIGIN), 'kept');
});

test('a file of another version, or of none, is not kept', () => {
    // A page that stayed open across a deploy still asks for the old files.
    assert.equal(plan(get('/static/js/connection.js?v=old00000', 'no-cors'), CONFIG, ORIGIN), 'network');
    assert.equal(plan(get('/static/leaflet/images/marker-icon.png', 'no-cors'), CONFIG, ORIGIN), 'network');
});

test('an uploaded photo or document is never kept', () => {
    assert.equal(plan(get('/media/horses/ace.jpg', 'no-cors'), CONFIG, ORIGIN), 'network');
    assert.equal(plan(get('/media/documents/passport.pdf', 'navigate'), CONFIG, ORIGIN), 'page');
});

test('C-vet is left to its own worker', () => {
    assert.equal(plan(get('/c-vet/'), CONFIG, ORIGIN), 'network');
    assert.equal(plan(get('/c-vet/api/yard/', 'cors'), CONFIG, ORIGIN), 'network');
});

test('another site is left alone', () => {
    assert.equal(plan({ method: 'GET', url: 'https://tile.openstreetmap.org/1/1/1.png', mode: 'no-cors' }, CONFIG, ORIGIN), 'network');
    assert.equal(plan({ method: 'GET', url: 'https://accounts.google.com/o/oauth2/auth', mode: 'navigate' }, CONFIG, ORIGIN), 'network');
});

test('the store is named for the version, so a deploy makes a new one', () => {
    assert.equal(cacheName(CONFIG), 'app-shell-abc12345');
    assert.equal(cacheName({ version: 'new' }), PREFIX + 'new');
});
