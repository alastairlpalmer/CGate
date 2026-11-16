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

    function money(pence) {
        return '\u00a3' + decimal(pence);
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

    // Split an amount between co-owners by their shares. The parts add up
    // to the amount exactly: the odd pence go to the largest remainders.
    // Shares that do not reach 100% leave the rest as `unallocated`.
    function splitByShare(totalPence, payers) {
        var list = (payers || []).map(function (payer) {
            var exact = totalPence * (Number(payer.share) || 0) / 100;
            return { name: payer.name, pence: Math.floor(exact), rest: exact - Math.floor(exact) };
        });
        var shareSum = (payers || []).reduce(function (sum, payer) { return sum + (Number(payer.share) || 0); }, 0);
        var target = Math.round(totalPence * Math.min(shareSum, 100) / 100);
        var given = list.reduce(function (sum, part) { return sum + part.pence; }, 0);
        var order = list.slice().sort(function (a, b) { return b.rest - a.rest; });
        for (var i = 0; given < target && order.length; i = (i + 1) % order.length) {
            order[i].pence += 1;
            given += 1;
        }
        return {
            parts: list.map(function (part) { return { name: part.name, pence: part.pence }; }),
            unallocated: totalPence - given
        };
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

    // "2026-11-03" -> "3 Nov", with the year when it is not `today`'s.
    function shortDate(day, today) {
        var parts = String(day || '').split('-');
        if (parts.length !== 3) return '';
        var text = +parts[2] + ' ' + MONTHS[+parts[1] - 1];
        return today && today.slice(0, 4) !== parts[0] ? text + ' ' + parts[0] : text;
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
    function unsentText(count, finishes) {
        if (count === 0) return finishes ? 'Finish not sent' : 'Everything is sent';
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

    var api = {
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
        visitSummary: visitSummary,
        addDays: addDays,
        shortDate: shortDate,
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
