// Run with: node --test static/js/tests   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const geo = require('../geo.js');

test('haversineMetres: known distances', () => {
    // London to Paris ≈ 343.5 km
    const d = geo.haversineMetres(51.5074, -0.1278, 48.8566, 2.3522);
    assert.ok(Math.abs(d / 1000 - 343.5) < 1.5, `got ${d}`);
    // One degree of latitude ≈ 111.2 km
    assert.ok(Math.abs(geo.haversineMetres(0, 0, 1, 0) - 111195) < 50);
    assert.equal(geo.haversineMetres(51, -2, 51, -2), 0);
});

test('haversineMetres: equator and antimeridian', () => {
    // One degree of longitude on the equator ≈ 111.2 km
    assert.ok(Math.abs(geo.haversineMetres(0, 10, 0, 11) - 111195) < 50);
    // Across the antimeridian: 179.9 E to 179.9 W is 0.2 degrees, not 359.8
    const across = geo.haversineMetres(0, 179.9, 0, -179.9);
    assert.ok(Math.abs(across - 22239) < 20, `got ${across}`);
    // Antipodes are half the circumference
    const half = geo.haversineMetres(0, 0, 0, 180);
    assert.ok(Math.abs(half - Math.PI * 6371000) < 1);
});

test('passesAccuracyGate', () => {
    assert.equal(geo.passesAccuracyGate(12), true);
    assert.equal(geo.passesAccuracyGate(100), true);
    assert.equal(geo.passesAccuracyGate(101), false);
    assert.equal(geo.passesAccuracyGate(2500), false);
    assert.equal(geo.passesAccuracyGate(undefined), false);
    assert.equal(geo.passesAccuracyGate(NaN), false);
    assert.equal(geo.passesAccuracyGate(-1), false);
    assert.equal(geo.passesAccuracyGate(150, 200), true);
});

test('formatDistance', () => {
    assert.equal(geo.formatDistance(12.4), '12 m');
    assert.equal(geo.formatDistance(999.4), '999 m');
    assert.equal(geo.formatDistance(1000), '1.0 km');
    assert.equal(geo.formatDistance(2345), '2.3 km');
    assert.equal(geo.formatDistance(-1), '');
    assert.equal(geo.formatDistance(undefined), '');
});

// A yard: three fields near each other and one far away, plus a site centre.
const here = { lat: 51.5480, lng: -2.0646 };
const metresNorth = (m) => m / 111195;
const locations = [
    { pk: 1, name: 'Grain store field', site: 'Somerford', lat: here.lat + metresNorth(40), lng: here.lng },
    { pk: 2, name: 'Top field', site: 'Somerford', lat: here.lat + metresNorth(120), lng: here.lng },
    { pk: 3, name: 'Far field', site: 'Somerford', lat: here.lat + metresNorth(900), lng: here.lng },
    { pk: 4, name: 'No point', site: 'Somerford', lat: null, lng: null },
    { pk: 5, name: 'Other yard', site: 'Colgate', lat: 52.9, lng: -1.1 },
];
const sites = [
    { name: 'Somerford', lat: here.lat + metresNorth(500), lng: here.lng, radius_m: 1500, count: 4 },
    { name: 'Colgate', lat: 52.9, lng: -1.1, radius_m: 1000, count: 1 },
    { name: 'No centre', lat: null, lng: null, radius_m: 1500, count: 2 },
];

test('ladder step 1: exactly one location within the radius', () => {
    const r = geo.resolveLadder(here, locations, sites, { nearRadiusM: 100 });
    assert.equal(r.step, 1);
    assert.equal(r.location.pk, 1);
    assert.ok(Math.abs(r.distance - 40) < 1);
    assert.deepEqual(r.alternatives, []);
});

test('ladder step 2: several within the radius → closest plus alternatives', () => {
    const r = geo.resolveLadder(here, locations, sites, { nearRadiusM: 150 });
    assert.equal(r.step, 2);
    assert.equal(r.location.pk, 1);
    assert.deepEqual(r.alternatives.map((a) => a.location.pk), [2]);
    // At most three alternatives, nearest first
    const crowd = [1, 2, 3, 4, 5, 6].map((i) => ({ pk: i, name: 'P' + i, site: 'S', lat: here.lat + metresNorth(i * 10), lng: here.lng }));
    const c = geo.resolveLadder(here, crowd, [], { nearRadiusM: 150 });
    assert.equal(c.location.pk, 1);
    assert.deepEqual(c.alternatives.map((a) => a.location.pk), [2, 3, 4]);
});

test('ladder step 3: on the site but not near a location', () => {
    const away = { lat: here.lat + metresNorth(-400), lng: here.lng };  // 440 m from the nearest field, 900 m from the centre
    const r = geo.resolveLadder(away, locations, sites, { nearRadiusM: 150 });
    assert.equal(r.step, 3);
    assert.equal(r.site.name, 'Somerford');
    assert.equal(r.count, 4);
});

test('ladder step 4: nothing → null; skips sites with no centre and locations without points', () => {
    const nowhere = { lat: 55.9, lng: -3.2 };
    assert.equal(geo.resolveLadder(nowhere, locations, sites, { nearRadiusM: 150 }), null);
    assert.equal(geo.resolveLadder(here, [], [], {}), null);
    assert.equal(geo.resolveLadder(null, locations, sites, {}), null);
    // Only the pointless location exists: no answer, no crash
    assert.equal(geo.resolveLadder(here, [locations[3]], [sites[2]], {}), null);
});

test('ladder uses the default radius when none is given', () => {
    assert.equal(geo.DEFAULT_NEAR_RADIUS_M, 150);
    const r = geo.resolveLadder(here, locations, sites);
    assert.equal(r.step, 2);
});

test('lastUsedIsFresh: two-hour window', () => {
    const now = 1_700_000_000_000;
    assert.equal(geo.lastUsedIsFresh({ pk: 3, at: now - 60_000 }, now), true);
    assert.equal(geo.lastUsedIsFresh({ pk: 3, at: now - 3 * 3600_000 }, now), false);
    assert.equal(geo.lastUsedIsFresh({ pk: 3, at: now + 60_000 }, now), false);
    assert.equal(geo.lastUsedIsFresh(null, now), false);
    assert.equal(geo.lastUsedIsFresh({ at: now }, now), false);
});
