// Run with: node --test 'static/js/tests/*.test.js'   (from horse_management/)
const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../../cvet/app_core.js');

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
    assert.equal(core.unsentText(0), 'All sent');
    assert.equal(core.unsentText(1), '1 note not sent');
    assert.equal(core.unsentText(3), '3 notes not sent');
});

test('outcome: created and duplicate both mean the server has the note', () => {
    assert.equal(core.outcome(201), 'sent');
    assert.equal(core.outcome(200), 'sent');
});

test('outcome: a note is never dropped because of a sign-in or a server fault', () => {
    assert.equal(core.outcome(401), 'signin');
    assert.equal(core.outcome(403), 'denied');
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

test('localDay: the phone\'s own calendar day', () => {
    assert.equal(core.localDay(new Date(2026, 10, 6, 23, 59)), '2026-11-06');
    assert.equal(core.localDay(new Date(2026, 0, 1, 0, 0)), '2026-01-01');
});

test('visitForDay: one visit for a day, a new one the next day', () => {
    let made = 0;
    const makeId = () => `visit-${++made}`;
    const first = core.visitForDay(undefined, '2026-11-16', makeId);
    assert.deepEqual(first, { id: 'visit-1', day: '2026-11-16', finished: false });
    assert.equal(core.visitForDay(first, '2026-11-16', makeId), first, 'same day: same visit');
    assert.equal(made, 1, 'no id is made for a note of the same visit');
    assert.deepEqual(core.visitForDay(first, '2026-11-17', makeId), { id: 'visit-2', day: '2026-11-17', finished: false });
});

test('visitForDay: a note after Finish visit starts a new visit the same day', () => {
    const finished = { id: 'visit-1', day: '2026-11-16', finished: true };
    assert.deepEqual(core.visitForDay(finished, '2026-11-16', () => 'visit-2'), { id: 'visit-2', day: '2026-11-16', finished: false });
});

test('unsentText: a finished visit that is not sent is not "everything"', () => {
    assert.equal(core.unsentText(0, 1), 'Finish not sent');
    assert.equal(core.unsentText(2, 1), '2 notes not sent');
    assert.equal(core.unsentText(0, 0), 'All sent');
});

test('toPence: prices as people type them', () => {
    assert.equal(core.toPence('65'), 6500);
    assert.equal(core.toPence('65.5'), 6550);
    assert.equal(core.toPence(' 65.50 '), 6550);
    assert.equal(core.toPence('0'), 0);
    assert.equal(core.toPence('0.07'), 7);
    for (const bad of ['', 'abc', '-5', '1.234', '1,50', '65.', '.5', '1e3', '123456', null, undefined]) {
        assert.equal(core.toPence(bad), null, String(bad));
    }
});

test('decimal and money: exact, with two places', () => {
    assert.equal(core.decimal(6500), '65.00');
    assert.equal(core.decimal(7), '0.07');
    assert.equal(core.decimal(123456), '1234.56');
    assert.equal(core.money(2750), '\u00a327.50');
});

test('toPence: sums that floats get wrong stay exact', () => {
    // 0.1 + 0.2 !== 0.3 in floating point; 10 + 20 === 30 in pence.
    assert.equal(core.toPence('0.1') + core.toPence('0.2'), core.toPence('0.3'));
    assert.equal(core.lineTotal({ qty: 3, unitPence: core.toPence('19.99') }), 5997);
});

test('vatPart: the VAT inside a price that includes it', () => {
    assert.equal(core.vatPart(6000, 20), 1000);
    assert.equal(core.vatPart(6500, 20), 1083);
    assert.equal(core.vatPart(6500, 0), 0);
});

test('splitByShare: the parts add up to the amount, to the penny', () => {
    const thirds = [{ name: 'A', share: '33.33' }, { name: 'B', share: '33.33' }, { name: 'C', share: '33.34' }];
    const split = core.splitByShare(10000, thirds);
    assert.deepEqual(split.parts.map((p) => p.pence), [3333, 3333, 3334]);
    assert.equal(split.unallocated, 0);

    const halves = core.splitByShare(6501, [{ name: 'A', share: '50.00' }, { name: 'B', share: '50.00' }]);
    assert.equal(halves.parts[0].pence + halves.parts[1].pence, 6501);
    assert.equal(Math.abs(halves.parts[0].pence - halves.parts[1].pence), 1);
});

test('splitByShare: one owner pays all; no owner leaves it all unallocated', () => {
    assert.deepEqual(core.splitByShare(6500, [{ name: 'Jo', share: '100.00' }]), {
        parts: [{ name: 'Jo', pence: 6500 }], unallocated: 0,
    });
    assert.deepEqual(core.splitByShare(6500, []), { parts: [], unallocated: 6500 });
    assert.deepEqual(core.splitByShare(6500, undefined), { parts: [], unallocated: 6500 });
});

test('splitByShare: the owners on record pay all of it when shares are under 100%', () => {
    // Decision 18: bill the owners present in full, in proportion.
    assert.deepEqual(core.splitByShare(10000, [{ name: 'A', share: '60.00' }]), {
        parts: [{ name: 'A', pence: 10000 }], unallocated: 0,
    });
    assert.deepEqual(core.splitByShare(10000, [{ name: 'A', share: '30.00' }, { name: 'B', share: '30.00' }]).parts.map((p) => p.pence), [5000, 5000]);
    const uneven = core.splitByShare(10000, [{ name: 'A', share: '60.00' }, { name: 'B', share: '20.00' }]);
    assert.deepEqual(uneven.parts.map((p) => p.pence), [7500, 2500]);
    assert.equal(uneven.unallocated, 0);
    const odd = core.splitByShare(1001, [{ name: 'A', share: '10.00' }, { name: 'B', share: '20.00' }]);
    assert.equal(odd.parts[0].pence + odd.parts[1].pence, 1001);
});

test('ownersText: owners when the yard is billed, payers when owners are billed', () => {
    const one = [{ name: 'Jo', share: '100.00' }];
    const two = [{ name: 'Jo', share: '30.00' }, { name: 'Ali', share: '30.00' }];
    assert.equal(core.ownersText([], 'yard'), 'No owner on record');
    assert.equal(core.ownersText(one, 'yard'), 'Owner: Jo');
    assert.equal(core.ownersText(one, 'owners'), 'Pays: Jo');
    // To the yard: the shares as the yard recorded them.
    assert.equal(core.ownersText(two, 'yard'), 'Owners: Jo 30% \u00b7 Ali 30%');
    // To owners: the part of the bill each one pays.
    assert.equal(core.ownersText(two, 'owners'), 'Pays: Jo 50% \u00b7 Ali 50%');
    assert.equal(core.ownersText(undefined, undefined), 'No owner on record');
});

test('addMonths: the same day, or the last day of a shorter month', () => {
    assert.equal(core.addMonths('2026-11-16', 3), '2027-02-16');
    assert.equal(core.addMonths('2027-01-31', 1), '2027-02-28');
    assert.equal(core.addMonths('2028-01-31', 1), '2028-02-29');
    assert.equal(core.addMonths('2026-11-30', 12), '2027-11-30');
});

test('suggestRecheck: a date from the words of the note', () => {
    const today = '2026-11-16';
    const date = (text) => (core.suggestRecheck(text, today) || {}).date;
    assert.equal(date('Mild heat left fore, no lameness at trot. Recheck in two weeks.'), '2026-11-30');
    assert.equal(date('re-check in 10 days'), '2026-11-26');
    assert.equal(date('Recheck in a week'), '2026-11-23');
    assert.equal(date('I will review her in 3 months'), '2027-02-16');
    assert.equal(date('Re-examine in a couple of weeks'), '2026-11-30');
    assert.equal(date('recheck in a fortnight'), '2026-11-30');
    assert.equal(date('Follow up next week'), '2026-11-23');
    assert.equal(date('See again tomorrow'), '2026-11-17');
    assert.equal(date('Recheck the near fore in six weeks'), '2026-12-28');
    assert.equal(date('Dictated: Recheck in Two Weeks'), '2026-11-30');
});

test('suggestRecheck: the last phrase wins, and it gives the words it used', () => {
    const found = core.suggestRecheck('Recheck in a week. No, recheck   in two weeks.', '2026-11-16');
    assert.deepEqual(found, { date: '2026-11-30', phrase: 'recheck in two weeks' });
});

test('suggestRecheck: no date from a note that does not ask for one', () => {
    const today = '2026-11-16';
    for (const text of [
        '', 'Lame for two weeks.', 'Sound. No recheck needed.', 'Booster given, next due in 12 months.',
        'Improved in 3 days on bute.', 'Owner to call in a week if no better.', null,
    ]) {
        assert.equal(core.suggestRecheck(text, today), null, String(text));
    }
});

test('visitSummary: horses, lines, payers and totals of one visit', () => {
    const horses = {
        1: { id: 1, payers: [{ name: 'Jo', share: '100.00' }] },
        2: { id: 2, payers: [{ name: 'Jo', share: '50.00' }, { name: 'Ali', share: '50.00' }] },
    };
    const entries = [
        {
            horseId: 1, horseName: 'Bramble', note: 'Mild heat.', followUp: '2026-11-30', status: 'sent',
            createdAt: '2026-11-16T10:00:00Z', photoBytes: 10, audioBytes: 0,
            lines: [{ description: 'Flu/Tet booster', qty: 1, unitPence: 6500, vatRate: '20.00' }],
        },
        {
            horseId: 2, horseName: 'Clover', note: '', followUp: '', status: 'pending',
            createdAt: '2026-11-16T10:05:00Z', photoBytes: 0, audioBytes: 0,
            lines: [
                { description: 'Flu/Tet booster', qty: 1, unitPence: 6500, vatRate: '20.00' },
                { description: 'Sedation', qty: 2, unitPence: 2750, vatRate: '20.00' },
            ],
        },
        {
            horseId: 1, horseName: 'Bramble', note: 'Second look.', followUp: '', status: 'pending',
            createdAt: '2026-11-16T10:10:00Z', photoBytes: 0, audioBytes: 5, lines: [],
        },
    ];
    const summary = core.visitSummary(entries, horses);
    assert.deepEqual(summary.horses.map((h) => [h.horseName, h.total, h.notes, h.unsent]), [
        ['Bramble', 6500, 2, 1],
        ['Clover', 12000, 0, 1],
    ]);
    assert.equal(summary.horses[0].followUp, '2026-11-30');
    assert.equal(summary.horses[0].photos, 1);
    assert.equal(summary.horses[0].recordings, 1);
    assert.equal(summary.total, 18500);
    assert.deepEqual(summary.payers, [{ name: 'Jo', pence: 12500 }, { name: 'Ali', pence: 6000 }]);
    assert.equal(summary.unallocated, 0);
    assert.equal(summary.payers.reduce((sum, p) => sum + p.pence, 0), summary.total);
    assert.equal(summary.vat, 1083 + 1083 + 917);
});

test('visitSummary: a horse that left the phone\'s list has no payer', () => {
    const summary = core.visitSummary([{
        horseId: 9, horseName: 'Gone', note: '', status: 'sent', createdAt: 'x',
        lines: [{ description: 'Visit fee', qty: 1, unitPence: 4000, vatRate: '20.00' }],
    }], {});
    assert.deepEqual(summary.payers, []);
    assert.equal(summary.unallocated, 4000);
});

test('addDays and shortDate', () => {
    assert.equal(core.addDays('2026-11-16', 14), '2026-11-30');
    assert.equal(core.addDays('2026-12-25', 14), '2027-01-08');
    assert.equal(core.addDays('2028-02-28', 1), '2028-02-29');
    assert.equal(core.shortDate('2026-11-03', '2026-11-16'), '3 Nov');
    assert.equal(core.shortDate('2027-01-08', '2026-11-16'), '8 Jan 2027');
    assert.equal(core.shortDate('', '2026-11-16'), '');
});

test('dueStatus: overdue, soon (14 days) and later', () => {
    assert.equal(core.dueStatus('2026-11-15', '2026-11-16'), 'overdue');
    assert.equal(core.dueStatus('2026-11-16', '2026-11-16'), 'soon');
    assert.equal(core.dueStatus('2026-11-30', '2026-11-16'), 'soon');
    assert.equal(core.dueStatus('2026-12-01', '2026-11-16'), 'later');
});

test('horseDue: the first thing that is overdue or soon', () => {
    const horse = { due: [
        { kind: 'vaccination', label: 'Flu/Tet', date: '2026-11-20' },
        { kind: 'follow_up', label: 'Recheck', date: '2026-11-10' },
        { kind: 'vaccination', label: 'Tetanus', date: '2027-06-01' },
    ] };
    assert.deepEqual(core.horseDue(horse, '2026-11-16'), { label: 'Recheck', date: '2026-11-10', status: 'overdue', more: 1 });
    assert.equal(core.horseDue({ due: [{ label: 'Flu', date: '2027-06-01' }] }, '2026-11-16'), null);
    assert.equal(core.horseDue({}, '2026-11-16'), null);
});

test('sortHorses: due horses first, most overdue at the top, then by name', () => {
    const horses = [
        { name: 'Willow' },
        { name: 'Clover', due: [{ label: 'Flu', date: '2026-11-25' }] },
        { name: 'Bramble' },
        { name: 'Echo', due: [{ label: 'Recheck', date: '2026-11-01' }] },
        { name: 'Dancer', due: [{ label: 'Flu', date: '2027-08-01' }] },
    ];
    assert.deepEqual(core.sortHorses(horses, '2026-11-16').map((h) => h.name), ['Echo', 'Clover', 'Bramble', 'Dancer', 'Willow']);
    assert.equal(horses[0].name, 'Willow', 'the list passed in is not reordered');
});

test('outcome: a visit the server does not know is dropped from the queue, not retried for ever', () => {
    assert.equal(core.outcome(404), 'failed');
});

// ── Clients, the item list and the record of a visit (plan, section 7g) ──

test('unsentText: a client or a horse that waits on the phone is said too', () => {
    assert.equal(core.unsentText(0, 0, 2), 'Changes not sent');
    assert.equal(core.unsentText(0, 1, 2), 'Finish not sent');
    assert.equal(core.unsentText(2, 0, 2), '2 notes not sent');
    assert.equal(core.unsentText(0, 0, 0), 'All sent');
});

test('matchesClient: every typed word is in the name, the contact or the kind', () => {
    const client = { name: 'Marsh Stud', contact_name: 'Sam Marsh', kind: 'stud' };
    assert.ok(core.matchesClient(client, ''));
    assert.ok(core.matchesClient(client, 'sam stud'));
    assert.ok(!core.matchesClient(client, 'racing'));
    assert.equal(core.kindLabel('racing'), 'Racing yard');
    assert.equal(core.kindLabel('nothing'), 'Client');
});

test('groupPrices: active prices under their categories, Other last, with a search', () => {
    const prices = [
        { id: 'a', name: 'Sedation', category: '', isActive: true },
        { id: 'b', name: 'Flu/Tet booster', category: 'Vaccination', isActive: true },
        { id: 'c', name: 'Tetanus', category: 'Vaccination', isActive: true },
        { id: 'd', name: 'Old item', category: 'Vaccination', isActive: false },
        { id: 'e', name: 'Rasp', category: 'Dental', isActive: true },
    ];
    const shape = (groups) => groups.map((group) => group.category + ': ' + group.items.map((item) => item.name).join(', '));
    assert.deepEqual(shape(core.groupPrices(prices)), ['Dental: Rasp', 'Vaccination: Flu/Tet booster, Tetanus', 'Other: Sedation']);
    assert.deepEqual(shape(core.groupPrices(prices, 'tet')), ['Vaccination: Flu/Tet booster, Tetanus']);
    assert.deepEqual(shape(core.groupPrices(prices, 'vacc flu')), ['Vaccination: Flu/Tet booster']);
    assert.deepEqual(shape(core.groupPrices(prices, '', 'Other')), ['Other: Sedation']);
    assert.deepEqual(core.priceCategories(prices), ['Dental', 'Vaccination', 'Other']);
});

test('a note reads the same from the phone and from the practice record', () => {
    const entry = {
        clientId: 'n1', visitId: 'v1', horseId: 7, horseName: 'Willow', note: 'Mild heat.', followUp: '2026-11-30',
        lines: [{ description: 'Sedation', qty: 2, unitPence: 2750, vatRate: '20.00' }],
        photo: null, photoId: 'p1', photoBytes: 900, audio: { id: 'a1', buffer: 1, type: 'audio/mp4' }, audioBytes: 5000,
        createdAt: '2026-11-16T10:00:00.000Z', status: 'pending', attempts: 1,
    };
    const local = core.noteFromEntry(entry, '/c-vet/api/files/');
    assert.deepEqual(local.lines, [{ description: 'Sedation', qty: 2, pence: 5500, vatRate: '20.00' }]);
    // The photo was sent: it is read from the server. The recording waits on the phone.
    assert.deepEqual(local.files.map((file) => [file.kind, file.url, !!file.local]),
        [['photo', '/c-vet/api/files/p1/', false], ['audio', '', true]]);
    const record = core.noteFromServer({
        id: 'n1', horse_id: 7, client_horse_id: null, horse_name: 'Willow', seen_on: '2026-11-16',
        written_at: '2026-11-16T10:00:00+00:00', author: 'Tim Hill', findings: 'Mild heat.', follow_up_date: '2026-11-30',
        lines: [{ description: 'Sedation', quantity: '2', total: '55.00', vat_rate: '20.00' }],
        files: [{ kind: 'audio', bytes: 5000, url: '/c-vet/api/files/a1/' }],
    });
    assert.equal(record.horseKey, local.horseKey);
    assert.deepEqual(record.lines, local.lines);
    assert.equal(record.status, 'sent');
    assert.equal(record.files[0].url, '/c-vet/api/files/a1/');
});

test('groupNotes and horseLine: one folded line for each horse of a visit', () => {
    const note = (horse, when, extra) => Object.assign({
        horseKey: horse, horseName: horse, when, lines: [], text: '', files: [], followUp: '', status: 'sent',
    }, extra);
    const rows = core.groupNotes([
        note('Clover', '2026-11-16T11:00:00Z', { lines: [{ pence: 6500 }], status: 'pending' }),
        note('Willow', '2026-11-16T10:00:00Z', { lines: [{ pence: 6000 }, { pence: 2750 }], text: 'Heat.', files: [{ kind: 'audio' }] }),
        note('Willow', '2026-11-16T12:00:00Z', { files: [{ kind: 'photo' }], followUp: '2026-11-30' }),
    ]);
    assert.deepEqual(rows.map((row) => [row.horseName, row.total, row.notes.length]), [['Willow', 8750, 2], ['Clover', 6500, 1]]);
    assert.equal(core.horseLine(rows[0]), '2 items \u00b7 note \u00b7 photo \u00b7 voice');
    assert.equal(core.horseLine(rows[1]), '1 item \u00b7 not sent yet');
    assert.equal(rows[0].followUp, '2026-11-30');
});

test('localVisits: the visits on the phone, from its notes, newest first', () => {
    const visits = core.localVisits([
        { visitId: 'v1', ownerId: 'c1', ownerName: 'Palmer Farm', createdAt: '2026-11-15T10:00:00' },
        { visitId: 'v2', ownerId: 'c2', ownerName: 'Marsh Stud', createdAt: '2026-11-16T10:00:00' },
        { visitId: 'v1', ownerId: 'c1', ownerName: 'Palmer Farm', createdAt: '2026-11-15T11:00:00' },
        { visitId: '', createdAt: '2026-11-16T10:00:00' },
    ]);
    assert.deepEqual(visits.map((visit) => [visit.id, visit.clientName, visit.day, visit.entries.length]),
        [['v2', 'Marsh Stud', '2026-11-16', 1], ['v1', 'Palmer Farm', '2026-11-15', 2]]);
});

test('oftenUsed: the active prices used on 3 notes or more, the most used first', () => {
    const prices = [
        { id: 'a', name: 'Sedation', isActive: true }, { id: 'b', name: 'Booster', isActive: true },
        { id: 'c', name: 'Rasp', isActive: true }, { id: 'd', name: 'Old', isActive: false },
    ];
    const note = (...ids) => ({ lines: ids.map((id) => ({ priceId: id })) });
    const entries = [note('a', 'b'), note('b'), note('b', 'd'), note('a', 'd'), note('a', 'd'), note('b', 'c'), note({})];
    assert.deepEqual(core.oftenUsed(prices, entries).map((price) => price.name), ['Booster', 'Sedation']);
    assert.deepEqual(core.oftenUsed(prices, []), []);
});

test('dayLabel and byDay: the headings of a timeline, the newest day first', () => {
    assert.equal(core.dayLabel('2026-11-24', '2026-11-24'), 'Today');
    assert.equal(core.dayLabel('2026-11-23', '2026-11-24'), 'Yesterday');
    assert.equal(core.dayLabel('2026-11-16', '2026-11-24'), 'Mon 16 Nov');
    assert.equal(core.dayLabel('2025-12-31', '2026-01-01'), 'Yesterday');
    assert.equal(core.dayLabel('2025-11-16', '2026-11-24'), 'Sun 16 Nov 2025');
    const groups = core.byDay([{ day: '2026-11-16', n: 1 }, { day: '2026-11-24', n: 2 }, { day: '2026-11-16', n: 3 }]);
    assert.deepEqual(groups.map((group) => [group.day, group.rows.map((row) => row.n)]),
        [['2026-11-24', [2]], ['2026-11-16', [1, 3]]]);
    assert.deepEqual(core.byDay([]), []);
});

test('money: a comma marks each thousand; decimal, for a box and the server, has none', () => {
    assert.equal(core.money(6500), '£65.00');
    assert.equal(core.money(128250), '£1,282.50');
    assert.equal(core.money(123456789), '£1,234,567.89');
    assert.equal(core.money(0), '£0.00');
    assert.equal(core.decimal(128250), '1282.50');
});

test('daysBetween: whole days, over a month end and a year end', () => {
    assert.equal(core.daysBetween('2026-11-24', '2026-12-08'), 14);
    assert.equal(core.daysBetween('2026-12-31', '2027-01-01'), 1);
    assert.equal(core.daysBetween('2026-11-24', '2026-11-24'), 0);
    assert.equal(core.daysBetween('2026-11-24', '2026-11-20'), -4);
});
