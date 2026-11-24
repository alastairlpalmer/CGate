/* C-vet: the parts with no browser in them, so Node can test them
   (static/js/tests/cvet_app_core.test.js). */
(function (root) {
    'use strict';

    // A random UUID (version 4). The server stores each note under the id
    // made here, so a note that is sent twice is stored once.
    function newId(cryptoObj) {
        if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
            return cryptoObj.randomUUID();
        }
        var bytes = new Uint8Array(16);
        if (cryptoObj && typeof cryptoObj.getRandomValues === 'function') {
            cryptoObj.getRandomValues(bytes);
        } else {
            for (var i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
        }
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        var hex = [];
        for (var j = 0; j < 16; j++) hex.push((bytes[j] + 0x100).toString(16).slice(1));
        return hex.slice(0, 4).join('') + '-' + hex.slice(4, 6).join('') + '-' +
            hex.slice(6, 8).join('') + '-' + hex.slice(8, 10).join('') + '-' +
            hex.slice(10, 16).join('');
    }

    // The calendar day on the phone, as YYYY-MM-DD.
    function localDay(date) {
        function two(n) { return (n < 10 ? '0' : '') + n; }
        return date.getFullYear() + '-' + two(date.getMonth() + 1) + '-' + two(date.getDate());
    }

    // The visit a new note belongs to: one visit for each day at the
    // yard. The same visit while the day is the same; a new one, with a
    // new id, on the first note of another day.
    function visitForDay(current, day, makeId) {
        if (current && current.id && current.day === day && !current.finished) return current;
        return { id: makeId(), day: day, finished: false };
    }

    // ── Money. Whole pence everywhere, so that sums are exact. ──────────

    // "65", "65.5", "65.50" -> 6500, 6550, 6550. Anything else -> null.
    function toPence(text) {
        var match = /^(\d{1,5})(?:\.(\d{1,2}))?$/.exec(String(text === undefined || text === null ? '' : text).trim());
        if (!match) return null;
        var pence = match[2] ? (match[2].length === 1 ? match[2] + '0' : match[2]) : '00';
        return parseInt(match[1], 10) * 100 + parseInt(pence, 10);
    }

    // 6500 -> "65.00": what goes in a price box and to the server.
    function decimal(pence) {
        var whole = Math.floor(Math.abs(pence) / 100);
        var rest = Math.abs(pence) % 100;
        return (pence < 0 ? '-' : '') + whole + '.' + (rest < 10 ? '0' : '') + rest;
    }

    // 128250 -> "£1,282.50": for the screen only. A comma marks each
    // thousand, as on an invoice.
    function money(pence) {
        var parts = decimal(pence).split('.');
        return '\u00a3' + parts[0].replace(/\B(?=(\d{3})+$)/g, ',') + '.' + parts[1];
    }

    function lineTotal(line) {
        return Math.round(line.qty * line.unitPence);
    }

    function entryTotal(entry) {
        return (entry.lines || []).reduce(function (sum, line) { return sum + lineTotal(line); }, 0);
    }

    // The VAT inside a price that includes it.
    function vatPart(grossPence, ratePercent) {
        var rate = Number(ratePercent) || 0;
        return Math.round(grossPence * rate / (100 + rate));
    }

    // Split an amount between the owners on record, in proportion to
    // their shares. The owners on record pay all of it: shares of 60%
    // and 20% pay three quarters and one quarter (plan, decision 18).
    // The parts add up to the amount exactly: the odd pence go to the
    // largest remainders. With no owner on record, all is `unallocated`.
    function splitByShare(totalPence, payers) {
        var shareSum = (payers || []).reduce(function (sum, payer) { return sum + (Number(payer.share) || 0); }, 0);
        if (!shareSum) return { parts: [], unallocated: totalPence };
        var list = payers.map(function (payer) {
            var exact = totalPence * (Number(payer.share) || 0) / shareSum;
            return { name: payer.name, pence: Math.floor(exact), rest: exact - Math.floor(exact) };
        });
        var given = list.reduce(function (sum, part) { return sum + part.pence; }, 0);
        var order = list.slice().sort(function (a, b) { return b.rest - a.rest; });
        for (var i = 0; given < totalPence; i = (i + 1) % order.length) {
            order[i].pence += 1;
            given += 1;
        }
        return {
            parts: list.map(function (part) { return { name: part.name, pence: part.pence }; }),
            unallocated: 0
        };
    }

    // The line about a horse's owners. When the yard is billed, they are
    // named as owners, for the yard's own invoices. When owners are
    // billed, the line says who pays and the part each one pays.
    function ownersText(payers, billTo) {
        var list = payers || [];
        if (!list.length) return 'No owner on record';
        var toOwners = billTo === 'owners';
        if (list.length === 1) return (toOwners ? 'Pays: ' : 'Owner: ') + list[0].name;
        var shareSum = list.reduce(function (sum, payer) { return sum + (Number(payer.share) || 0); }, 0);
        return (toOwners ? 'Pays: ' : 'Owners: ') + list.map(function (payer) {
            var share = Number(payer.share) || 0;
            var percent = toOwners && shareSum ? Math.round(share * 100 / shareSum) : share;
            return payer.name + ' ' + percent + '%';
        }).join(' \u00b7 ');
    }

    // Everything the end-of-visit screen shows, from the notes of one
    // visit and the horse list on the phone.
    function visitSummary(entries, horsesById) {
        var byHorse = {};
        var horses = [];
        var payerTotals = {};
        var payerOrder = [];
        var total = 0;
        var vat = 0;
        var unallocated = 0;
        entries.slice().sort(function (a, b) {
            return a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0;
        }).forEach(function (entry) {
            var key = String(entry.horseId);
            var row = byHorse[key];
            if (!row) {
                row = byHorse[key] = {
                    horseId: entry.horseId, horseName: entry.horseName, lines: [], total: 0,
                    notes: 0, photos: 0, recordings: 0, followUp: '', unsent: 0
                };
                horses.push(row);
            }
            if (entry.note) row.notes += 1;
            if (entry.photoBytes) row.photos += 1;
            if (entry.audioBytes) row.recordings += 1;
            if (entry.followUp) row.followUp = entry.followUp;
            if (entry.status !== 'sent') row.unsent += 1;
            var entryPence = 0;
            (entry.lines || []).forEach(function (line) {
                var pence = lineTotal(line);
                row.lines.push({ description: line.description, qty: line.qty, pence: pence });
                entryPence += pence;
                vat += vatPart(pence, line.vatRate);
            });
            row.total += entryPence;
            total += entryPence;
            var horse = horsesById[key];
            var split = splitByShare(entryPence, horse ? horse.payers : []);
            unallocated += split.unallocated;
            split.parts.forEach(function (part) {
                if (!(part.name in payerTotals)) {
                    payerTotals[part.name] = 0;
                    payerOrder.push(part.name);
                }
                payerTotals[part.name] += part.pence;
            });
        });
        return {
            horses: horses,
            payers: payerOrder.map(function (name) { return { name: name, pence: payerTotals[name] }; }),
            unallocated: unallocated,
            total: total,
            vat: vat
        };
    }

    // ── Dates, as YYYY-MM-DD text. No time zones to get wrong. ──────────

    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    function addDays(day, days) {
        var parts = day.split('-');
        var date = new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2] + days));
        return date.toISOString().slice(0, 10);
    }

    // The same day of the month, `months` later. 31 Jan + 1 -> 28 Feb.
    function addMonths(day, months) {
        var parts = day.split('-');
        var first = new Date(Date.UTC(+parts[0], +parts[1] - 1 + months, 1));
        var last = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
        first.setUTCDate(Math.min(+parts[2], last));
        return first.toISOString().slice(0, 10);
    }

    // ── A recheck date from the words of the note ───────────────────────
    //
    // "Mild heat left fore. Recheck in two weeks." -> two weeks from today.
    // The vet speaks or types the note; the app offers the date; the vet
    // taps to use it. Nothing is set without that tap.

    var NUMBERS = {
        a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6,
        seven: 7, eight: 8, nine: 9, ten: 10, eleven: 11, twelve: 12, couple: 2
    };
    var AGAIN = '(?:re-?check|re-?examine|re-?assess|re-?visit|review|check\\s+again|see\\s+(?:again|him|her|it|them)|follow[\\s-]?up)';
    var COUNT = '(a\\s+couple\\s+of|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|\\d{1,2})';
    var WHEN = new RegExp(
        '\\b' + AGAIN + '\\b[^.;!?\\n]{0,40}?\\b(?:' +
        '(?:in|after)\\s+' + COUNT + '\\s+(day|week|month)s?\\b' +
        '|(?:in\\s+a\\s+)?(fortnight)\\b' +
        '|(next\\s+week)\\b' +
        '|(tomorrow)\\b)', 'gi');

    // { date: 'YYYY-MM-DD', phrase: the words it came from }, or null.
    // The last such phrase in the note wins: "recheck in a week... no,
    // recheck in two weeks" gives two weeks.
    function suggestRecheck(text, today) {
        var found = null;
        var match;
        WHEN.lastIndex = 0;
        while ((match = WHEN.exec(String(text || ''))) !== null) {
            var date;
            if (match[2]) {
                var word = match[1].toLowerCase().replace(/\s+/g, ' ');
                var count = word === 'a couple of' ? 2 : (NUMBERS[word] || parseInt(word, 10));
                var unit = match[2].toLowerCase();
                date = unit === 'month' ? addMonths(today, count) : addDays(today, count * (unit === 'week' ? 7 : 1));
            } else if (match[3]) {
                date = addDays(today, 14);
            } else if (match[4]) {
                date = addDays(today, 7);
            } else {
                date = addDays(today, 1);
            }
            found = { date: date, phrase: match[0].replace(/\s+/g, ' ').trim() };
        }
        return found;
    }

    // "2026-11-03" -> "3 Nov", with the year when it is not `today`'s.
    function shortDate(day, today) {
        var parts = String(day || '').split('-');
        if (parts.length !== 3) return '';
        var text = +parts[2] + ' ' + MONTHS[+parts[1] - 1];
        return today && today.slice(0, 4) !== parts[0] ? text + ' ' + parts[0] : text;
    }

    // Whole days from one day to another: 14 from 24 Nov to 8 Dec.
    function daysBetween(from, to) {
        function at(day) { var parts = day.split('-'); return Date.UTC(+parts[0], +parts[1] - 1, +parts[2]); }
        return Math.round((at(to) - at(from)) / 86400000);
    }

    var DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

    // The heading of a day on a timeline: "Today", "Yesterday", "Mon 16 Nov",
    // with the year when it is not `today`'s.
    function dayLabel(day, today) {
        if (day === today) return 'Today';
        if (day === addDays(today, -1)) return 'Yesterday';
        var parts = String(day || '').split('-');
        if (parts.length !== 3) return '';
        return DAYS[new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2])).getUTCDay()] + ' ' + shortDate(day, today);
    }

    // Rows that each have a `day`, as [{ day, rows }], the newest day first.
    function byDay(rows) {
        var groups = [];
        rows.slice().sort(function (a, b) { return a.day < b.day ? 1 : a.day > b.day ? -1 : 0; }).forEach(function (row) {
            var last = groups[groups.length - 1];
            if (last && last.day === row.day) last.rows.push(row);
            else groups.push({ day: row.day, rows: [row] });
        });
        return groups;
    }

    // overdue: before today. soon: in the next 14 days. later: after that.
    function dueStatus(day, today) {
        if (day < today) return 'overdue';
        return day <= addDays(today, 14) ? 'soon' : 'later';
    }

    // The first thing due for a horse that is overdue or soon, or null.
    function horseDue(horse, today) {
        var items = (horse.due || []).filter(function (item) {
            return dueStatus(item.date, today) !== 'later';
        }).sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
        if (!items.length) return null;
        return { label: items[0].label, date: items[0].date, status: dueStatus(items[0].date, today), more: items.length - 1 };
    }

    // Horses with something due first, the most overdue at the top; the
    // rest by name.
    function sortHorses(horses, today) {
        return horses.slice().sort(function (a, b) {
            var dueA = horseDue(a, today);
            var dueB = horseDue(b, today);
            if (dueA && dueB && dueA.date !== dueB.date) return dueA.date < dueB.date ? -1 : 1;
            if (!!dueA !== !!dueB) return dueA ? -1 : 1;
            return a.name.localeCompare(b.name);
        });
    }

    // Notes still to send, oldest first: the order they are sent in.
    function unsent(entries) {
        return entries
            .filter(function (entry) { return entry.status !== 'sent'; })
            .sort(function (a, b) { return a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0; });
    }

    // `finishes`: visits the vet finished that the server has not heard of.
    // `changes`: clients and horses made on the phone and not sent yet.
    function unsentText(count, finishes, changes) {
        if (count === 0) return finishes ? 'Finish not sent' : changes ? 'Changes not sent' : 'All sent';
        return count === 1 ? '1 note not sent' : count + ' notes not sent';
    }

    // What to do with a reply from the entries endpoint.
    //   sent    the server has the note: mark it sent
    //   signin  the session ended: stop, keep the note, ask for a sign-in
    //   denied  this login may not use C-vet: stop, keep the note, say so
    //   later   the server or the network failed: stop, try again later
    //   failed  the server will never take this note: keep it, show why
    function outcome(status) {
        if (status === 200 || status === 201) return 'sent';
        if (status === 404) return 'failed';
        if (status === 401) return 'signin';
        if (status === 403) return 'denied';
        if (status === 400 || status === 409 || status === 413) return 'failed';
        return 'later';
    }

    function formatBytes(bytes) {
        if (typeof bytes !== 'number' || !isFinite(bytes)) return 'not known';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    // The first recording format this browser can make. iPhones make
    // audio/mp4; Chrome makes audio/webm. Empty when none is known: the
    // browser then picks its own.
    function pickAudioType(isSupported) {
        var types = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
        for (var i = 0; i < types.length; i++) {
            if (isSupported(types[i])) return types[i];
        }
        return '';
    }

    // A horse matches when every typed word is in its name, owner,
    // location, microchip or passport number.
    function matchesHorse(horse, query) {
        var words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
        if (!words.length) return true;
        var text = [horse.name, horse.owner, horse.location, horse.microchip, horse.passport]
            .join(' ').toLowerCase();
        return words.every(function (word) { return text.indexOf(word) !== -1; });
    }

    // ── Clients ─────────────────────────────────────────────────────────

    var KINDS = [
        ['yard', 'Livery yard'], ['stud', 'Stud'], ['racing', 'Racing yard'],
        ['competition', 'Competition yard'], ['private', 'Private owner'], ['other', 'Other']
    ];

    function kindLabel(kind) {
        for (var i = 0; i < KINDS.length; i++) if (KINDS[i][0] === kind) return KINDS[i][1];
        return 'Client';
    }

    // A client matches when every typed word is in its name, its contact
    // or its kind.
    function matchesClient(client, query) {
        var words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
        var text = [client.name, client.contact_name, kindLabel(client.kind)].join(' ').toLowerCase();
        return words.every(function (word) { return text.indexOf(word) !== -1; });
    }

    // ── The item list: prices under their categories ────────────────────

    // [{ category, items }] of the active prices that match the search,
    // categories by name with "Other" (no category) last. `category`
    // keeps one group only; empty or undefined keeps them all.
    function groupPrices(prices, query, category) {
        var words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
        var groups = {};
        var names = [];
        (prices || []).forEach(function (price) {
            if (!price.isActive) return;
            var name = price.category || 'Other';
            if (category && name !== category) return;
            var text = (price.name + ' ' + name).toLowerCase();
            if (!words.every(function (word) { return text.indexOf(word) !== -1; })) return;
            if (!groups[name]) { groups[name] = []; names.push(name); }
            groups[name].push(price);
        });
        names.sort(function (a, b) {
            if ((a === 'Other') !== (b === 'Other')) return a === 'Other' ? 1 : -1;
            return a.localeCompare(b);
        });
        return names.map(function (name) {
            return { category: name, items: groups[name].sort(function (a, b) { return a.name.localeCompare(b.name); }) };
        });
    }

    // The active prices the vet used on 3 notes or more, the most used
    // first: at most 5. From the notes on the phone, so it needs no signal.
    function oftenUsed(prices, entries) {
        var uses = {};
        (entries || []).forEach(function (entry) {
            (entry.lines || []).forEach(function (line) {
                if (line.priceId) uses[line.priceId] = (uses[line.priceId] || 0) + 1;
            });
        });
        return (prices || []).filter(function (price) {
            return price.isActive && uses[price.id] >= 3;
        }).sort(function (a, b) {
            return uses[b.id] - uses[a.id] || a.name.localeCompare(b.name);
        }).slice(0, 5);
    }

    // The category names of the active prices, in the order of the list.
    function priceCategories(prices) {
        return groupPrices(prices).map(function (group) { return group.category; });
    }

    // ── A note, as the screen shows it ──────────────────────────────────
    //
    // One shape for a note that is on the phone and for a note from the
    // practice's records, so one piece of the screen shows both.

    // `filesUrl`: where the server serves a file, by its id.
    function noteFromEntry(entry, filesUrl) {
        var files = [];
        [['photo', entry.photo, entry.photoId, entry.photoBytes],
            ['audio', entry.audio, entry.audioId, entry.audioBytes]].forEach(function (item) {
            if (!item[3]) return;
            var id = item[1] ? item[1].id : item[2];
            files.push({
                kind: item[0], bytes: item[3], local: item[1] || null,
                // A sent file is in the practice's records, not on the phone.
                url: !item[1] && id && filesUrl ? filesUrl + id + '/' : ''
            });
        });
        return {
            id: entry.clientId, visitId: entry.visitId || '', horseKey: String(entry.horseId),
            horseName: entry.horseName, when: entry.createdAt, day: localDay(new Date(entry.createdAt)),
            author: '', text: entry.note || '', followUp: entry.followUp || '',
            lines: (entry.lines || []).map(function (line) {
                return { description: line.description, qty: line.qty, pence: lineTotal(line), vatRate: line.vatRate };
            }),
            files: files, status: entry.status, error: entry.lastError || '', attempts: entry.attempts || 0,
            entry: entry
        };
    }

    function noteFromServer(note) {
        return {
            id: note.id, visitId: '', horseKey: String(note.client_horse_id || note.horse_id || note.horse_name),
            horseName: note.horse_name, when: note.written_at, day: note.seen_on,
            author: note.author || '', text: note.findings || '', followUp: note.follow_up_date || '',
            lines: (note.lines || []).map(function (line) {
                return {
                    description: line.description, qty: Number(line.quantity),
                    pence: toPence(line.total) || 0, vatRate: line.vat_rate
                };
            }),
            files: (note.files || []).map(function (file) {
                return { kind: file.kind, bytes: file.bytes, local: null, url: file.url };
            }),
            status: 'sent', error: '', attempts: 0, entry: null
        };
    }

    // The notes of one visit, one row for each horse, in the order the
    // horses were seen: what a folded visit shows.
    function groupNotes(notes) {
        var byHorse = {};
        var rows = [];
        notes.slice().sort(function (a, b) {
            return a.when < b.when ? -1 : a.when > b.when ? 1 : 0;
        }).forEach(function (note) {
            var row = byHorse[note.horseKey];
            if (!row) {
                row = byHorse[note.horseKey] = {
                    horseKey: note.horseKey, horseName: note.horseName, notes: [], items: 0, total: 0,
                    texts: 0, photos: 0, recordings: 0, followUp: '', unsent: 0
                };
                rows.push(row);
            }
            row.notes.push(note);
            note.lines.forEach(function (line) { row.items += 1; row.total += line.pence; });
            if (note.text) row.texts += 1;
            note.files.forEach(function (file) {
                if (file.kind === 'photo') row.photos += 1; else row.recordings += 1;
            });
            if (note.followUp) row.followUp = note.followUp;
            if (note.status !== 'sent') row.unsent += 1;
        });
        return rows;
    }

    // "2 items · note · voice": the one line of a folded horse.
    function horseLine(row) {
        var parts = [];
        if (row.items) parts.push(row.items === 1 ? '1 item' : row.items + ' items');
        if (row.texts) parts.push(row.texts === 1 ? 'note' : row.texts + ' notes');
        if (row.photos) parts.push(row.photos === 1 ? 'photo' : row.photos + ' photos');
        if (row.recordings) parts.push('voice');
        if (row.unsent) parts.push('not sent yet');
        return parts.join(' \u00b7 ');
    }

    // The visits on the phone, from its notes: [{ id, clientId,
    // clientName, day, entries }], the newest day first.
    function localVisits(entries) {
        var byId = {};
        var list = [];
        entries.forEach(function (entry) {
            if (!entry.visitId) return;
            var visit = byId[entry.visitId];
            if (!visit) {
                visit = byId[entry.visitId] = {
                    id: entry.visitId, clientId: entry.ownerId || '', clientName: entry.ownerName || '',
                    day: localDay(new Date(entry.createdAt)), entries: []
                };
                list.push(visit);
            }
            visit.entries.push(entry);
        });
        return list.sort(function (a, b) { return a.day < b.day ? 1 : a.day > b.day ? -1 : 0; });
    }

    var api = {
        KINDS: KINDS,
        kindLabel: kindLabel,
        matchesClient: matchesClient,
        groupPrices: groupPrices,
        priceCategories: priceCategories,
        oftenUsed: oftenUsed,
        noteFromEntry: noteFromEntry,
        noteFromServer: noteFromServer,
        groupNotes: groupNotes,
        horseLine: horseLine,
        localVisits: localVisits,
        newId: newId,
        localDay: localDay,
        visitForDay: visitForDay,
        toPence: toPence,
        decimal: decimal,
        money: money,
        lineTotal: lineTotal,
        entryTotal: entryTotal,
        vatPart: vatPart,
        splitByShare: splitByShare,
        ownersText: ownersText,
        addMonths: addMonths,
        suggestRecheck: suggestRecheck,
        visitSummary: visitSummary,
        addDays: addDays,
        shortDate: shortDate,
        dayLabel: dayLabel,
        daysBetween: daysBetween,
        byDay: byDay,
        dueStatus: dueStatus,
        horseDue: horseDue,
        sortHorses: sortHorses,
        unsent: unsent,
        unsentText: unsentText,
        outcome: outcome,
        formatBytes: formatBytes,
        pickAudioType: pickAudioType,
        matchesHorse: matchesHorse
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    } else {
        root.CvetCore = api;
    }
})(typeof window !== 'undefined' ? window : this);
