// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../../cvet/proto_core.js');

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test('newId: uses randomUUID when the browser has it', () => {
    assert.equal(core.newId({ randomUUID: () => 'from-the-browser' }), 'from-the-browser');
});

test('newId: builds a version 4 UUID from random bytes', () => {
    const cryptoObj = { getRandomValues: (bytes) => { bytes.fill(0xff); return bytes; } };
    assert.equal(core.newId(cryptoObj), 'ffffffff-ffff-4fff-bfff-ffffffffffff');
});

test('newId: still gives a valid, different id with no crypto at all', () => {
    const a = core.newId(undefined);
    const b = core.newId(undefined);
    assert.match(a, UUID_V4);
    assert.match(b, UUID_V4);
    assert.notEqual(a, b);
});

test('unsent: drops sent notes and puts the oldest first', () => {
    const entries = [
        { clientId: 'c', status: 'pending', createdAt: '2026-11-16T10:02:00.000Z' },
        { clientId: 'a', status: 'sent', createdAt: '2026-11-16T10:00:00.000Z' },
        { clientId: 'b', status: 'failed', createdAt: '2026-11-16T10:01:00.000Z' },
    ];
    assert.deepEqual(core.unsent(entries).map((e) => e.clientId), ['b', 'c']);
    assert.equal(entries[0].clientId, 'c', 'the list passed in is not reordered');
});

test('unsentText: the counter the vet reads', () => {
    assert.equal(core.unsentText(0), 'Everything is sent');
    assert.equal(core.unsentText(1), '1 note not sent');
    assert.equal(core.unsentText(3), '3 notes not sent');
});

test('outcome: created and duplicate both mean the server has the note', () => {
    assert.equal(core.outcome(201), 'sent');
    assert.equal(core.outcome(200), 'sent');
});

test('outcome: a note is never dropped because of a sign-in or a server fault', () => {
    assert.equal(core.outcome(401), 'signin');
    assert.equal(core.outcome(403), 'signin');
    assert.equal(core.outcome(429), 'later');
    assert.equal(core.outcome(500), 'later');
    assert.equal(core.outcome(502), 'later');
    assert.equal(core.outcome(0), 'later');
});

test('outcome: a note the server refuses is kept and marked', () => {
    assert.equal(core.outcome(400), 'failed');
    assert.equal(core.outcome(409), 'failed');
    assert.equal(core.outcome(413), 'failed');
});

test('formatBytes', () => {
    assert.equal(core.formatBytes(512), '512 B');
    assert.equal(core.formatBytes(2048), '2 KB');
    assert.equal(core.formatBytes(5 * 1024 * 1024), '5.0 MB');
    assert.equal(core.formatBytes(undefined), 'not known');
});

test('pickAudioType: the first format the browser can record', () => {
    assert.equal(core.pickAudioType((type) => type === 'audio/mp4'), 'audio/mp4');
    assert.equal(core.pickAudioType((type) => type.startsWith('audio/webm')), 'audio/webm;codecs=opus');
    assert.equal(core.pickAudioType(() => false), '');
});

test('matchesHorse: every word must match, in any field', () => {
    const horse = { name: 'Bramble', owner: 'Jo Smith', location: 'Top Field', microchip: '985100012345678', passport: '' };
    assert.ok(core.matchesHorse(horse, ''));
    assert.ok(core.matchesHorse(horse, 'bram'));
    assert.ok(core.matchesHorse(horse, 'smith top'));
    assert.ok(core.matchesHorse(horse, '5678'));
    assert.ok(!core.matchesHorse(horse, 'bramble jones'));
});
