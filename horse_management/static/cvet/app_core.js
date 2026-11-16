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
        if (current && current.id && current.day === day) return current;
        return { id: makeId(), day: day };
    }

    // Notes still to send, oldest first: the order they are sent in.
    function unsent(entries) {
        return entries
            .filter(function (entry) { return entry.status !== 'sent'; })
            .sort(function (a, b) { return a.createdAt < b.createdAt ? -1 : a.createdAt > b.createdAt ? 1 : 0; });
    }

    function unsentText(count) {
        if (count === 0) return 'Everything is sent';
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
