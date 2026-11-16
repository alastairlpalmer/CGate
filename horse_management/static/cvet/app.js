/* C-vet: the page (docs/C_VET_PLAN.md, sections 4, 5 and 7c).

   A web app that holds the yard's horse list, the practice's price list
   and the vet's notes on the phone, with or without a signal, and sends
   each note to the practice's records exactly once.

   Where things are kept:
     - the page and its files: the service worker's store (templates/cvet/sw.js)
     - horses, prices, notes and the visit: IndexedDB, below
     - nothing in cookies or localStorage

   Rules this file keeps:
     - a note is never removed from the phone until the server has said it
       holds it (or the person taps Remove, Clear this phone or Sign out)
     - every note, line, file and visit has an id made on the phone, sent
       with every attempt, so the server stores it once
     - no screen waits for the network: it reads and writes the phone's
       storage, and the sync runs behind it
     - money is whole pence (app_core.js), so sums are exact
     - text from the server or the person goes on the page with
       textContent only, never as HTML
*/
(function () {
    'use strict';

    var core = window.CvetCore;
    var config = document.body.dataset;
    var $ = function (id) { return document.getElementById(id); };

    var MAX_RECORDING_MS = 60 * 1000;
    var PHOTO_MAX_EDGE = 1600;
    var AUTO_SYNC_MS = 60 * 1000;
    var REFRESH_AFTER_MS = 12 * 60 * 60 * 1000;
    var DENIED_TEXT = 'This login may not use C-vet. Your notes are safe on this phone. Ask the administrator.';
    // Recheck choices: label and days from today.
    var FOLLOW_UPS = [['None', 0], ['1 wk', 7], ['2 wk', 14], ['4 wk', 28], ['3 mo', 91]];

    var state = {
        view: 'horses',
        horses: [],
        horsesById: {},
        prices: [],
        entries: [],
        meta: {},          // downloadedAt, userId, userName, practice, testMode, visit, finishQueue, lastSyncAt
        draft: null,       // the horse screen: { horse, lines, followUp, photo, audio }
        selecting: false,
        selected: {},      // horse id -> true, while several horses are picked
        editingPrice: null,
        syncing: false,
        recorder: null,
        storage: null,
        persisted: null,
        workerState: 'none',
        workerReady: false, // the page is stored: the next open needs no signal
        openMs: null        // from the tap on the icon to the horse list on the screen
    };

    function today() { return core.localDay(new Date()); }
    function newId() { return core.newId(window.crypto); }

    // ── IndexedDB ────────────────────────────────────────────────────────

    var STORES = ['meta', 'horses', 'prices', 'entries'];
    var dbPromise = null;

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise(function (resolve, reject) {
            var request = indexedDB.open('cvet', 2);
            request.onupgradeneeded = function () {
                var db = request.result;
                var keys = { meta: 'key', horses: 'id', prices: 'id', entries: 'clientId' };
                STORES.forEach(function (name) {
                    if (!db.objectStoreNames.contains(name)) db.createObjectStore(name, { keyPath: keys[name] });
                });
            };
            request.onsuccess = function () { resolve(request.result); };
            request.onerror = function () { reject(request.error); };
        });
        return dbPromise;
    }

    // Run fn(store) in one transaction; resolve when the transaction has
    // been written, not when the request returns. A note is "saved" only
    // after this resolves.
    function withStore(name, mode, fn) {
        return openDb().then(function (db) {
            return new Promise(function (resolve, reject) {
                var tx = db.transaction(name, mode);
                var result;
                tx.oncomplete = function () { resolve(result); };
                tx.onerror = function () { reject(tx.error); };
                tx.onabort = function () { reject(tx.error || new Error('Storage refused the write')); };
                var request = fn(tx.objectStore(name));
                if (request) request.onsuccess = function () { result = request.result; };
            });
        });
    }

    function getAll(name) {
        return withStore(name, 'readonly', function (store) { return store.getAll(); });
    }

    function put(name, value) {
        return withStore(name, 'readwrite', function (store) { store.put(value); });
    }

    function putAll(name, values) {
        return withStore(name, 'readwrite', function (store) {
            values.forEach(function (value) { store.put(value); });
        });
    }

    function remove(name, key) {
        return withStore(name, 'readwrite', function (store) { store.delete(key); });
    }

    function replaceAll(name, values) {
        return withStore(name, 'readwrite', function (store) {
            store.clear();
            values.forEach(function (value) { store.put(value); });
        });
    }

    function setMeta(key, value) {
        state.meta[key] = value;
        return put('meta', { key: key, value: value });
    }

    function clearEverything() {
        return Promise.all(STORES.map(function (name) {
            return withStore(name, 'readwrite', function (store) { store.clear(); });
        }));
    }

    // ── The network ──────────────────────────────────────────────────────

    // Always resolves. kind is one of:
    //   ok       a JSON reply (any status): { status, data }
    //   signin   the server sent a sign-in page or a redirect to one
    //   offline  no reply at all (no signal, or it timed out)
    function request(url, options, timeoutMs) {
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
        var settings = Object.assign({ credentials: 'same-origin', cache: 'no-store', headers: {} }, options || {});
        settings.headers = Object.assign({ Accept: 'application/json' }, settings.headers);
        if (controller) settings.signal = controller.signal;

        return fetch(url, settings).then(function (response) {
            var type = response.headers.get('Content-Type') || '';
            if (response.redirected || type.indexOf('application/json') === -1) {
                // Two-step sign-in and the sign-in page answer with HTML.
                if (response.status === 404) return { kind: 'ok', status: 404, data: {} };
                return { kind: response.status >= 500 ? 'offline' : 'signin', status: response.status };
            }
            return response.json().then(function (data) {
                return { kind: 'ok', status: response.status, data: data };
            });
        }).catch(function () {
            return { kind: 'offline', status: 0 };
        }).then(function (result) {
            if (timer) clearTimeout(timer);
            return result;
        });
    }

    function post(url, form, csrf, timeoutMs) {
        return request(url, { method: 'POST', body: form, headers: { 'X-CSRFToken': csrf } }, timeoutMs || 30000);
    }

    // Who is signed in, and a CSRF token for the uploads.
    function getSession() {
        return request(config.sessionUrl, {}, 20000).then(function (result) {
            if (result.kind === 'ok' && result.status === 200) return { kind: 'ok', session: result.data };
            if (result.kind === 'ok') return { kind: result.status === 403 ? 'denied' : 'signin' };
            return { kind: result.kind };
        });
    }

    // ── Files ────────────────────────────────────────────────────────────

    function readBuffer(blob) {
        if (typeof blob.arrayBuffer === 'function') return blob.arrayBuffer();
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () { resolve(reader.result); };
            reader.onerror = function () { reject(reader.error); };
            reader.readAsArrayBuffer(blob);
        });
    }

    // Empty when the browser cannot hash (a page not on HTTPS). The server
    // then stores the file without the "arrived complete" check.
    function sha256(buffer) {
        if (!window.crypto || !window.crypto.subtle) return Promise.resolve('');
        return window.crypto.subtle.digest('SHA-256', buffer).then(function (digest) {
            return Array.prototype.map.call(new Uint8Array(digest), function (byte) {
                return (byte + 0x100).toString(16).slice(1);
            }).join('');
        }).catch(function () { return ''; });
    }

    // A phone photo is 3 to 5 MB. Make it at most 1600 px on the long edge
    // before it is stored: less space on the phone, a faster send on a
    // weak signal. If the browser cannot do this, the photo is kept as it is.
    function shrinkPhoto(file) {
        if (typeof createImageBitmap !== 'function') return Promise.resolve(file);
        return createImageBitmap(file).then(function (bitmap) {
            var scale = Math.min(1, PHOTO_MAX_EDGE / Math.max(bitmap.width, bitmap.height));
            var canvas = document.createElement('canvas');
            canvas.width = Math.round(bitmap.width * scale);
            canvas.height = Math.round(bitmap.height * scale);
            canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
            if (typeof bitmap.close === 'function') bitmap.close();
            return new Promise(function (resolve) {
                canvas.toBlob(function (blob) { resolve(blob || file); }, 'image/jpeg', 0.82);
            });
        }).catch(function () { return file; });
    }

    // Files are kept as { buffer, type }, not as Blobs: some iPhone
    // versions lose Blobs that are stored in IndexedDB.
    function toStored(blob) {
        return readBuffer(blob).then(function (buffer) {
            return sha256(buffer).then(function (hash) {
                return { buffer: buffer, type: blob.type || '', bytes: buffer.byteLength, sha256: hash };
            });
        });
    }

    // ── Small helpers for the screen ─────────────────────────────────────

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function button(className, text, onClick) {
        var node = el('button', className, text);
        node.type = 'button';
        node.addEventListener('click', onClick);
        return node;
    }

    function fill(list, nodes) {
        // Built off the page and put on it in one step: one layout pass
        // however long the list is.
        var fragment = document.createDocumentFragment();
        nodes.forEach(function (node) { fragment.appendChild(node); });
        list.textContent = '';
        list.appendChild(fragment);
    }

    function timeText(iso) {
        if (!iso) return 'never';
        var date = new Date(iso);
        return core.shortDate(core.localDay(date), today()) + ', ' +
            date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    // The line under the top bar. A plain message goes away by itself;
    // one that asks the vet to do something (`stays`) does not.
    var sayTimer = null;
    function say(text, stays) {
        $('sync-message').textContent = text;
        if (sayTimer) clearTimeout(sayTimer);
        sayTimer = text && !stays ? setTimeout(function () { $('sync-message').textContent = ''; }, 8000) : null;
    }

    function unsentEntries() { return core.unsent(state.entries); }
    function finishQueue() { return state.meta.finishQueue || []; }

    // The notes of the visit that is open now (or was finished last).
    function visitEntries() {
        var visit = state.meta.visit;
        if (!visit) return [];
        return state.entries.filter(function (entry) { return entry.visitId === visit.id; });
    }

    // Who the bill for a visit goes to at this yard: 'yard' (the yard
    // invoices its owners) or 'owners'.
    function billTo() { return (state.meta.yard && state.meta.yard.billTo) || 'yard'; }
    function yardName() { return (state.meta.yard && state.meta.yard.name) || 'the yard'; }

    function payersText(horse) { return core.ownersText(horse.payers, billTo()); }

    function dueText(due) {
        var when = core.shortDate(due.date, today());
        return due.label + (due.status === 'overdue' ? ' overdue ' : ' due ') + when;
    }

    // ── The bar at the top, and the tabs ─────────────────────────────────

    function renderBar() {
        var chip = $('signal');
        chip.textContent = navigator.onLine ? 'Signal' : 'No signal';
        chip.className = navigator.onLine ? 'chip' : 'chip off';

        var count = unsentEntries().length;
        var finishes = finishQueue().length;
        var node = $('unsent');
        node.textContent = core.unsentText(count, finishes);
        node.className = 'chip count ' + (count || finishes ? 'pending' : 'clear');
        // Said on the screen for as long as it is true: these notes are
        // test notes, not clinical records.
        $('test-mode').hidden = state.meta.testMode !== true;

        var visit = state.meta.visit;
        var horses = {};
        if (visit && !visit.finished) {
            visitEntries().forEach(function (entry) { horses[entry.horseId] = true; });
        }
        var badge = $('visit-badge');
        badge.hidden = Object.keys(horses).length === 0;
        badge.textContent = String(Object.keys(horses).length);
    }

    function show(view) {
        if (state.view === 'horse' && view !== 'horse') stopRecording();
        state.view = view;
        ['horses', 'horse', 'visit', 'prices', 'phone'].forEach(function (name) {
            $('view-' + name).hidden = name !== view;
        });
        Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (tab) {
            var current = tab.dataset.view === view || (view === 'horse' && tab.dataset.view === 'horses');
            if (current) tab.setAttribute('aria-current', 'page');
            else tab.removeAttribute('aria-current');
        });
        $('select-bar').hidden = !(view === 'horses' && state.selecting);
        render();
        window.scrollTo(0, 0);
    }

    function render() {
        renderBar();
        if (state.view === 'horses') renderHorses();
        else if (state.view === 'horse') renderHorse();
        else if (state.view === 'visit') renderVisit();
        else if (state.view === 'prices') renderPrices();
        else if (state.view === 'phone') renderPhone();
    }

    // ── Horses ───────────────────────────────────────────────────────────

    function renderHorses() {
        var query = $('search').value;
        var day = today();
        var seen = {};
        var visit = state.meta.visit;
        if (visit && !visit.finished) visitEntries().forEach(function (entry) { seen[entry.horseId] = true; });

        var rows = core.sortHorses(state.horses, day).filter(function (horse) {
            return core.matchesHorse(horse, query);
        }).map(function (horse) {
            var item = el('li');
            var row = button('horse', undefined, function () {
                if (state.selecting) toggleSelected(horse);
                else openHorse(horse);
            });
            if (state.selecting) {
                row.appendChild(el('span', 'mark' + (state.selected[horse.id] ? ' picked' : ''), state.selected[horse.id] ? '✓' : ''));
                row.setAttribute('aria-pressed', state.selected[horse.id] ? 'true' : 'false');
            } else if (seen[horse.id]) {
                row.appendChild(el('span', 'mark done', '✓'));
            }
            var text = el('span', 'text');
            text.appendChild(el('span', 'strong', horse.name));
            var line = [horse.owner, horse.location].filter(Boolean).join(' · ');
            if (line) text.appendChild(el('small', '', line));
            // Chip and passport numbers take a line each horse. Show
            // them only while the vet is searching, to check a match.
            var ids = query ? [
                horse.microchip ? 'Chip ' + horse.microchip : '',
                horse.passport ? 'Passport ' + horse.passport : ''
            ].filter(Boolean).join(' · ') : '';
            if (ids) text.appendChild(el('small', '', ids));
            row.appendChild(text);
            var due = core.horseDue(horse, day);
            if (due) row.appendChild(el('span', 'due ' + due.status, dueText(due)));
            item.appendChild(row);
            return item;
        });
        fill($('horses'), rows);
        var none = state.horses.length === 0;
        // Where the vet is: the yard is the client, and these are its horses.
        $('yard-line').textContent = none ? ''
            : yardName() + ' · ' + (state.horses.length === 1 ? '1 horse' : state.horses.length + ' horses');
        $('no-horses').hidden = !none;
        $('search').parentNode.hidden = none;

        var toggle = $('select-toggle');
        toggle.textContent = state.selecting ? 'Done' : 'Select';
        toggle.className = 'button slim' + (state.selecting ? ' on' : '');
        var picked = Object.keys(state.selected).length;
        $('select-bar').hidden = !state.selecting;
        $('select-count').textContent = picked === 1 ? '1 horse selected' : picked + ' horses selected';
        $('select-add').disabled = picked === 0;
    }

    function toggleSelected(horse) {
        if (state.selected[horse.id]) delete state.selected[horse.id];
        else state.selected[horse.id] = true;
        renderHorses();
    }

    function toggleSelecting() {
        state.selecting = !state.selecting;
        state.selected = {};
        renderHorses();
    }

    // Several horses at once: one item, added to every selected horse.
    function openPicker() {
        var active = state.prices.filter(function (price) { return price.isActive; });
        fill($('picker-tiles'), active.map(function (price) {
            return priceTile(price, function () { addToSelected(price); });
        }));
        $('picker-empty').hidden = active.length > 0;
        $('picker').hidden = false;
    }

    function addToSelected(price) {
        var horses = Object.keys(state.selected).map(function (id) { return state.horsesById[id]; }).filter(Boolean);
        $('picker').hidden = true;
        if (!horses.length) return;
        var now = new Date();
        var visit = currentVisit(now);
        var entries = horses.map(function (horse) {
            return newEntry(horse, visit, now, { lines: [lineFrom(price)] });
        });
        setMeta('visit', visit).then(function () {
            return putAll('entries', entries);
        }).then(function () {
            Array.prototype.push.apply(state.entries, entries);
            state.selecting = false;
            state.selected = {};
            say(price.name + ' added to ' + (horses.length === 1 ? '1 horse.' : horses.length + ' horses.'));
            render();
            sync(false);
        }).catch(function (error) {
            say('The phone could not save this: ' + error.message);
        });
    }

    // ── One horse ────────────────────────────────────────────────────────

    function priceTile(price, onClick) {
        var tile = button('tile', undefined, onClick);
        tile.appendChild(document.createTextNode(price.name));
        tile.appendChild(el('small', '', core.money(price.pence)));
        return tile;
    }

    function lineFrom(price) {
        return {
            id: newId(), priceId: price.id, description: price.name,
            qty: 1, unitPence: price.pence, vatRate: price.vatRate
        };
    }

    function openHorse(horse) {
        stopRecording();
        state.draft = { horse: horse, lines: [], followUp: '', photo: null, audio: null };
        $('note').value = '';
        $('photo').value = '';
        $('photo-state').textContent = '';
        $('audio-state').textContent = '';
        $('entry-message').textContent = '';
        $('history-message').textContent = '';
        state.olderNotes = null;
        setPreview(null);
        show('horse');
    }

    function draftIsEmpty() {
        var draft = state.draft;
        return !draft || (!draft.lines.length && !draft.photo && !draft.audio && !draft.followUp && !$('note').value.trim());
    }

    function leaveHorse() {
        if (!draftIsEmpty() && !window.confirm('Leave without saving this horse?')) return false;
        stopRecording();
        state.draft = null;
        setPreview(null);
        return true;
    }

    function renderHorse() {
        var draft = state.draft;
        if (!draft) { show('horses'); return; }
        var horse = draft.horse;
        var day = today();
        $('horse-name').textContent = horse.name;
        $('horse-ids').textContent = [
            horse.location,
            horse.microchip ? 'Chip ' + horse.microchip : '',
            horse.passport ? 'Passport ' + horse.passport : ''
        ].filter(Boolean).join(' · ');
        $('horse-payers').textContent = payersText(horse);

        var dueBox = $('horse-due');
        fill(dueBox, (horse.due || []).map(function (item) {
            var status = core.dueStatus(item.date, day);
            var text = item.label + (status === 'overdue' ? ' overdue ' : ' due ') + core.shortDate(item.date, day);
            return el('span', status === 'later' ? 'muted' : 'due ' + status, text + ' ');
        }));

        fill($('tiles'), state.prices.filter(function (price) {
            return price.isActive;
        }).map(function (price) {
            return priceTile(price, function () { addLine(lineFrom(price)); });
        }).concat([button('tile', 'Other item', function () {
            addLine({ id: newId(), priceId: '', description: '', qty: 1, unitPence: 0, vatRate: '20.00', custom: true });
        })]));

        renderLines();
        renderFollowUps();
        renderHistory();
    }

    function addLine(line) {
        var same = !line.custom && state.draft.lines.filter(function (existing) {
            return existing.priceId === line.priceId && existing.unitPence === line.unitPence;
        })[0];
        if (same) same.qty += 1;       // a second tap on a tile: two of them
        else state.draft.lines.push(line);
        renderLines();
    }

    function renderLines() {
        var draft = state.draft;
        fill($('lines'), draft.lines.map(function (line) {
            var item = el('li');
            if (line.custom) {
                var name = el('input');
                name.type = 'text';
                name.maxLength = 200;
                name.placeholder = 'What was done';
                name.value = line.description;
                name.setAttribute('aria-label', 'Item');
                name.addEventListener('input', function () { line.description = name.value; });
                item.appendChild(name);
            } else {
                item.appendChild(el('span', 'strong', line.description));
            }
            var controls = el('div', 'line-controls');
            var total = el('span', 'line-total', core.money(core.lineTotal(line)));
            controls.appendChild(button('step', '−', function () {
                if (line.qty > 1) line.qty -= 1;
                else draft.lines.splice(draft.lines.indexOf(line), 1);
                renderLines();
            }));
            controls.appendChild(el('span', 'qty', String(line.qty)));
            controls.appendChild(button('step', '+', function () { line.qty += 1; renderLines(); }));
            var price = el('input');
            price.type = 'text';
            price.inputMode = 'decimal';
            price.value = core.decimal(line.unitPence);
            price.setAttribute('aria-label', 'Price for ' + (line.description || 'item'));
            // The price can be changed for this horse. The total follows
            // as the vet types; a price that is not a number is marked
            // and stops Save.
            price.addEventListener('input', function () {
                var pence = core.toPence(price.value);
                line.badPrice = pence === null;
                price.className = line.badPrice ? 'bad' : '';
                if (!line.badPrice) line.unitPence = pence;
                total.textContent = core.money(core.lineTotal(line));
                renderLinesTotal();
            });
            controls.appendChild(price);
            controls.appendChild(total);
            item.appendChild(controls);
            return item;
        }));
        renderLinesTotal();
    }

    function renderLinesTotal() {
        var pence = state.draft.lines.reduce(function (sum, line) { return sum + core.lineTotal(line); }, 0);
        $('lines-total').textContent = state.draft.lines.length ? 'Total ' + core.money(pence) : '';
    }

    // A recheck date from the words of the note ("recheck in two weeks"),
    // typed or spoken. It is offered; the vet taps to use it.
    function renderSuggestion() {
        var draft = state.draft;
        var node = $('recheck-suggestion');
        var found = draft ? core.suggestRecheck($('note').value, today()) : null;
        if (!found || found.date === draft.followUp) { node.hidden = true; return; }
        node.hidden = false;
        node.textContent = 'Use ' + core.shortDate(found.date, today()) + ': you said “' + found.phrase + '”';
        node.onclick = function () {
            draft.followUp = found.date;
            renderFollowUps();
        };
    }

    function renderFollowUps() {
        var draft = state.draft;
        var day = today();
        fill($('followups'), FOLLOW_UPS.map(function (choice) {
            var date = choice[1] ? core.addDays(day, choice[1]) : '';
            var tile = button('tile' + (draft.followUp === date ? ' on' : ''), choice[0], function () {
                draft.followUp = date;
                renderFollowUps();
            });
            return tile;
        }));
        $('follow-up').value = draft.followUp;
        renderSuggestion();
    }

    function renderHistory() {
        var horse = state.draft.horse;
        var mine = state.entries.filter(function (entry) {
            return String(entry.horseId) === String(horse.id);
        }).sort(function (a, b) { return a.createdAt < b.createdAt ? 1 : -1; });
        var known = {};
        var rows = mine.map(function (entry) {
            known[entry.clientId] = true;
            return entryRow(entry, true);
        });
        (state.olderNotes || []).forEach(function (note) {
            if (!known[note.id]) rows.push(serverNoteRow(note));
        });
        fill($('history'), rows);
        $('no-history').hidden = rows.length > 0;
    }

    // One note that is on this phone.
    function entryRow(entry, canRemove) {
        var item = el('li', 'entry');
        var label = entry.status === 'sent' ? 'Sent ' + timeText(entry.sentAt)
            : entry.status === 'failed' ? 'Not sent: ' + (entry.lastError || 'the server refused it')
            : 'Not sent yet';
        item.appendChild(el('small', 'state ' + entry.status, timeText(entry.createdAt) + ' · ' + label));
        (entry.lines || []).forEach(function (line) {
            item.appendChild(el('small', '', (line.qty > 1 ? line.qty + ' × ' : '') + line.description + ' ' + core.money(core.lineTotal(line))));
        });
        if (entry.note) item.appendChild(el('small', 'wrap', entry.note));
        var parts = [];
        if (entry.followUp) parts.push('Recheck ' + core.shortDate(entry.followUp, today()));
        if (entry.photoBytes) parts.push('photo');
        if (entry.audioBytes) parts.push('voice');
        if (entry.attempts > 1) parts.push(entry.attempts + ' tries');
        if (parts.length) item.appendChild(el('small', '', parts.join(' · ')));
        if (canRemove && entry.status !== 'sent') {
            item.appendChild(button('link', 'Remove', function () { removeEntry(entry); }));
        }
        return item;
    }

    // One note from the practice's records, fetched with a signal and
    // not kept on the phone.
    function serverNoteRow(note) {
        var item = el('li', 'entry');
        item.appendChild(el('small', 'state sent', core.shortDate(note.seen_on, today()) + ' · ' + (note.author || 'Practice record')));
        (note.lines || []).forEach(function (line) {
            item.appendChild(el('small', '', (Number(line.quantity) > 1 ? Number(line.quantity) + ' × ' : '') + line.description + ' £' + line.total));
        });
        if (note.findings) item.appendChild(el('small', 'wrap', note.findings));
        var parts = [];
        if (note.follow_up_date) parts.push('Recheck ' + core.shortDate(note.follow_up_date, today()));
        var photos = (note.files || []).filter(function (file) { return file.kind === 'photo'; }).length;
        var voices = (note.files || []).length - photos;
        if (photos) parts.push(photos === 1 ? 'photo' : photos + ' photos');
        if (voices) parts.push('voice');
        if (parts.length) item.appendChild(el('small', '', parts.join(' · ')));
        return item;
    }

    function loadOlderNotes() {
        var horse = state.draft && state.draft.horse;
        if (!horse) return;
        var message = $('history-message');
        message.textContent = 'Loading…';
        request(config.notesUrl + '?horse=' + encodeURIComponent(horse.id), {}, 20000).then(function (result) {
            if (!state.draft || state.draft.horse !== horse) return;
            if (result.kind === 'ok' && result.status === 200) {
                state.olderNotes = result.data.notes;
                message.textContent = result.data.notes.length ? '' : 'The practice has no notes for this horse.';
                renderHistory();
            } else if (result.kind === 'offline') {
                message.textContent = 'No signal. Older notes need a signal.';
            } else {
                message.textContent = 'Sign in again to load older notes.';
            }
        });
    }

    function removeEntry(entry) {
        if (!window.confirm('Remove this note? It was not sent.')) return;
        remove('entries', entry.clientId).then(function () {
            state.entries.splice(state.entries.indexOf(entry), 1);
            render();
        });
    }

    var previewUrl = null;
    function setPreview(stored) {
        var audio = $('audio-preview');
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        previewUrl = null;
        if (!stored) {
            audio.hidden = true;
            audio.removeAttribute('src');
            return;
        }
        previewUrl = URL.createObjectURL(new Blob([stored.buffer], { type: stored.type }));
        audio.src = previewUrl;
        audio.hidden = false;
    }

    function onPhotoChosen() {
        var file = $('photo').files && $('photo').files[0];
        if (!file || !state.draft) return;
        var draft = state.draft;
        $('photo-state').textContent = 'Preparing the photo…';
        shrinkPhoto(file).then(toStored).then(function (stored) {
            if (state.draft !== draft) return;
            draft.photo = stored;
            $('photo-state').textContent = 'Photo ready (' + core.formatBytes(stored.bytes) + ').';
        }).catch(function (error) {
            $('photo-state').textContent = 'The photo could not be read: ' + error.message;
        });
    }

    function toggleRecording() {
        if (state.recorder) { stopRecording(); return; }
        if (!state.draft) return;
        var draft = state.draft;
        if (typeof window.MediaRecorder !== 'function' || !navigator.mediaDevices) {
            $('audio-state').textContent = 'This browser cannot record voice.';
            return;
        }
        navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
            var type = core.pickAudioType(function (t) { return window.MediaRecorder.isTypeSupported(t); });
            var recorder = new window.MediaRecorder(stream, type ? { mimeType: type } : undefined);
            var chunks = [];
            var started = Date.now();
            var timer = setTimeout(stopRecording, MAX_RECORDING_MS);
            recorder.ondataavailable = function (event) {
                if (event.data && event.data.size) chunks.push(event.data);
            };
            recorder.onstop = function () {
                clearTimeout(timer);
                stream.getTracks().forEach(function (track) { track.stop(); });
                state.recorder = null;
                $('record').textContent = 'Record voice';
                $('record').classList.remove('recording');
                var blob = new Blob(chunks, { type: recorder.mimeType || type || 'audio/mp4' });
                if (!blob.size) {
                    $('audio-state').textContent = 'Nothing was recorded. Try again.';
                    return;
                }
                var seconds = Math.max(1, Math.round((Date.now() - started) / 1000));
                toStored(blob).then(function (stored) {
                    if (state.draft !== draft) return;
                    draft.audio = stored;
                    $('audio-state').textContent = 'Voice recorded: ' + seconds + ' s (' +
                        core.formatBytes(stored.bytes) + ').';
                    setPreview(stored);
                });
            };
            state.recorder = recorder;
            recorder.start();
            $('record').textContent = 'Stop recording';
            $('record').classList.add('recording');
            $('audio-state').textContent = 'Recording… 60 seconds at most.';
        }).catch(function (error) {
            $('audio-state').textContent = 'The microphone is not available: ' + error.message;
        });
    }

    function stopRecording() {
        if (state.recorder && state.recorder.state !== 'inactive') state.recorder.stop();
    }

    // One visit for each day at the yard. Its id is made here, with the
    // first note of the day, and goes with every note after it. A note
    // after Finish visit starts a new one.
    function currentVisit(now) {
        return core.visitForDay(state.meta.visit, core.localDay(now), newId);
    }

    function newEntry(horse, visit, now, parts) {
        var photo = parts.photo || null;
        var audio = parts.audio || null;
        return {
            clientId: newId(),
            visitId: visit.id,
            horseId: horse.id,
            horseName: horse.name,
            note: parts.note || '',
            followUp: parts.followUp || '',
            lines: (parts.lines || []).map(function (line) {
                return {
                    id: line.id, priceId: line.priceId || '', description: line.description.trim(),
                    qty: line.qty, unitPence: line.unitPence, vatRate: line.vatRate
                };
            }),
            photo: photo ? { id: newId(), buffer: photo.buffer, type: photo.type, sha256: photo.sha256 } : null,
            photoBytes: photo ? photo.bytes : 0,
            audio: audio ? { id: newId(), buffer: audio.buffer, type: audio.type, sha256: audio.sha256 } : null,
            audioBytes: audio ? audio.bytes : 0,
            createdAt: now.toISOString(),
            status: 'pending',
            attempts: 0,
            sentAt: null,
            lastError: ''
        };
    }

    function saveDraft() {
        var draft = state.draft;
        if (!draft) return;
        var message = $('entry-message');
        if (state.recorder) { message.textContent = 'Stop the recording first.'; return; }
        var note = $('note').value.trim();
        if (draft.lines.some(function (line) { return line.badPrice; })) {
            message.textContent = 'A price is not a number. Check the box marked in red.';
            return;
        }
        if (draft.lines.some(function (line) { return !line.description.trim(); })) {
            message.textContent = 'Type what the other item is.';
            return;
        }
        if (!note && !draft.lines.length && !draft.photo && !draft.audio) {
            message.textContent = 'Tap an item, type a note, take a photo or record your voice first.';
            return;
        }
        var now = new Date();
        var visit = currentVisit(now);
        var entry = newEntry(draft.horse, visit, now, {
            note: note, followUp: draft.followUp, lines: draft.lines, photo: draft.photo, audio: draft.audio
        });
        $('save').disabled = true;
        askToKeepStorage();
        setMeta('visit', visit).then(function () {
            return put('entries', entry);
        }).then(function () {
            state.entries.push(entry);
            state.draft = null;
            setPreview(null);
            say(draft.horse.name + ' saved.');
            show('horses');
            refreshStorage();
            sync(false);
        }).catch(function (error) {
            // The note is still on the screen: nothing is lost.
            message.textContent = 'The phone could not save this note: ' + error.message;
        }).then(function () {
            $('save').disabled = false;
        });
    }

    // ── The visit ────────────────────────────────────────────────────────

    function renderVisit() {
        var visit = state.meta.visit;
        var entries = visitEntries();
        var summary = core.visitSummary(entries, state.horsesById);
        var day = today();
        $('visit-title').textContent = visit ? 'Visit · ' + core.shortDate(visit.day, day) : 'Visit';
        var toOwners = billTo() === 'owners';
        $('visit-bill').textContent = !entries.length ? ''
            : toOwners ? 'Bill to each owner' : 'Bill to ' + yardName();
        $('visit-empty').hidden = entries.length > 0;

        fill($('visit-horses'), summary.horses.map(function (row) {
            var item = el('li');
            var horse = state.horsesById[row.horseId];
            var line = button('horse', undefined, function () { if (horse) openHorse(horse); });
            var text = el('span', 'text');
            text.appendChild(el('span', 'strong', row.horseName));
            row.lines.forEach(function (bill) {
                text.appendChild(el('small', '', (bill.qty > 1 ? bill.qty + ' × ' : '') + bill.description + ' ' + core.money(bill.pence)));
            });
            var parts = [];
            if (row.notes) parts.push(row.notes === 1 ? 'note' : row.notes + ' notes');
            if (row.photos) parts.push(row.photos === 1 ? 'photo' : row.photos + ' photos');
            if (row.recordings) parts.push('voice');
            if (row.followUp) parts.push('recheck ' + core.shortDate(row.followUp, day));
            if (row.unsent) parts.push('not sent yet');
            if (parts.length) text.appendChild(el('small', 'wrap', parts.join(' · ')));
            text.appendChild(el('small', 'wrap', horse ? payersText(horse) : 'No owner on record'));
            line.appendChild(text);
            line.appendChild(el('span', 'amount', core.money(row.total)));
            item.appendChild(line);
            return item;
        }));

        $('visit-money').hidden = entries.length === 0;
        var payers = summary.payers.map(function (payer) {
            var item = el('li', 'pay');
            item.appendChild(el('span', '', payer.name));
            item.appendChild(el('span', 'amount', core.money(payer.pence)));
            return item;
        });
        if (summary.unallocated) {
            var item = el('li', 'pay');
            item.appendChild(el('span', '', 'No owner on record'));
            item.appendChild(el('span', 'amount', core.money(summary.unallocated)));
            payers.push(item);
        }
        fill($('visit-payers'), toOwners ? payers : []);
        $('visit-payers-title').hidden = !toOwners;
        var count = summary.horses.length;
        $('visit-total').textContent = (count === 1 ? '1 horse' : count + ' horses') + ' · Total ' + core.money(summary.total);
        $('visit-vat').textContent = 'Includes VAT of ' + core.money(summary.vat) + '. ' +
            (toOwners ? 'The practice confirms who pays when the notes arrive.'
                : yardName() + ' invoices each owner for their horse.');

        var finished = !!(visit && visit.finished);
        $('finish').hidden = entries.length === 0 || finished;
        var waiting = visit && finishQueue().some(function (item) { return item.id === visit.id; });
        $('visit-state').textContent = !finished ? ''
            : 'Visit finished ' + timeText(visit.finishedAt) + '. ' +
              (waiting || summary.horses.some(function (row) { return row.unsent; })
                  ? 'Not all sent yet: keep the app open when you have a signal.'
                  : 'All sent to the practice.');
    }

    function finishVisit() {
        var visit = state.meta.visit;
        var entries = visitEntries();
        if (!visit || visit.finished || !entries.length) return;
        var summary = core.visitSummary(entries, state.horsesById);
        var count = summary.horses.length;
        var question = 'Finish this visit? ' + (count === 1 ? '1 horse' : count + ' horses') + ', ' + core.money(summary.total) + '.';
        if (!window.confirm(question)) return;
        var finishedAt = new Date().toISOString();
        var done = { id: visit.id, day: visit.day, finished: true, finishedAt: finishedAt };
        setMeta('visit', done).then(function () {
            return setMeta('finishQueue', finishQueue().concat([{ id: visit.id, finishedAt: finishedAt }]));
        }).then(function () {
            say('Visit finished.');
            render();
            sync(false);
        });
    }

    // ── Prices ───────────────────────────────────────────────────────────

    function renderPrices() {
        fill($('prices'), state.prices.map(function (price) {
            var item = el('li');
            var row = button('price' + (price.isActive ? '' : ' hidden-price'), undefined, function () { editPrice(price); });
            var text = el('span', 'text');
            text.appendChild(el('span', 'strong', price.name));
            text.appendChild(el('small', '', (price.isActive ? '' : 'Hidden · ') + 'VAT ' + Number(price.vatRate) + '%'));
            row.appendChild(text);
            row.appendChild(el('span', 'amount', core.money(price.pence)));
            item.appendChild(row);
            return item;
        }));
        var editing = state.editingPrice;
        $('price-form-title').textContent = editing ? 'Change a price' : 'Add a price';
        $('price-hide').hidden = !editing;
        $('price-hide').textContent = editing && !editing.isActive ? 'Show' : 'Hide';
        $('price-cancel').hidden = !editing;
    }

    function editPrice(price) {
        state.editingPrice = price;
        $('price-name').value = price ? price.name : '';
        $('price-amount').value = price ? core.decimal(price.pence) : '';
        $('price-vat').value = price ? String(Number(price.vatRate)) : '20';
        $('price-message').textContent = '';
        renderPrices();
        if (price) $('price-name').focus();
    }

    function fromServerPrice(item) {
        return { id: item.id, name: item.name, pence: core.toPence(item.price) || 0, vatRate: item.vat_rate, isActive: item.is_active };
    }

    function savePrice(event, flipActive) {
        if (event) event.preventDefault();
        var message = $('price-message');
        var editing = state.editingPrice;
        var name = $('price-name').value.trim();
        var pence = core.toPence($('price-amount').value);
        var vat = core.toPence($('price-vat').value);
        if (!name) { message.textContent = 'Type a name.'; return; }
        if (pence === null) { message.textContent = 'Type the price as a number, for example 65 or 65.50.'; return; }
        if (vat === null || vat >= 10000) { message.textContent = 'Type the VAT rate as a number, for example 20.'; return; }
        var form = new FormData();
        form.append('id', editing ? editing.id : newId());
        form.append('name', name);
        form.append('price', core.decimal(pence));
        form.append('vat_rate', core.decimal(vat));
        var active = editing ? editing.isActive : true;
        if (flipActive) active = !active;
        form.append('is_active', active ? 'true' : 'false');
        message.textContent = 'Saving…';
        getSession().then(function (result) {
            if (result.kind === 'offline') { message.textContent = 'No signal. A price change needs a signal.'; return; }
            if (result.kind !== 'ok') { message.textContent = result.kind === 'denied' ? DENIED_TEXT : 'Sign in again to change a price.'; return; }
            return post(config.pricesUrl, form, result.session.csrf).then(function (reply) {
                if (reply.kind !== 'ok' || (reply.status !== 200 && reply.status !== 201)) {
                    message.textContent = reply.kind === 'offline' ? 'No signal. The price was not saved.' : 'The price was not saved.';
                    return;
                }
                var saved = fromServerPrice(reply.data);
                state.prices = state.prices.filter(function (price) { return price.id !== saved.id; }).concat([saved])
                    .sort(function (a, b) { return a.name.localeCompare(b.name); });
                return put('prices', saved).then(function () {
                    editPrice(null);
                    $('price-message').textContent = saved.name + ' saved.';
                });
            });
        });
    }

    // ── This phone ───────────────────────────────────────────────────────

    function installed() {
        return window.navigator.standalone === true ||
            (typeof window.matchMedia === 'function' && window.matchMedia('(display-mode: standalone)').matches);
    }

    // What this phone can do. Sent with every note, so the receipts on
    // the server show it too.
    function clientInfo() {
        var canRecord = typeof window.MediaRecorder === 'function' &&
            !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
        return {
            version: config.version,
            userAgent: navigator.userAgent,
            standalone: installed(),
            workerState: state.workerState,
            workerReady: state.workerReady,
            workerControls: !!(navigator.serviceWorker && navigator.serviceWorker.controller),
            backgroundSync: 'serviceWorker' in navigator && 'SyncManager' in window,
            online: navigator.onLine,
            canRecord: canRecord,
            audioType: canRecord ? core.pickAudioType(function (type) {
                return window.MediaRecorder.isTypeSupported(type);
            }) : '',
            storageUsed: state.storage ? state.storage.usage : null,
            storageQuota: state.storage ? state.storage.quota : null,
            storagePersisted: state.persisted,
            horses: state.horses.length,
            openMs: state.openMs,
            downloadedAt: state.meta.downloadedAt || null
        };
    }

    function renderPhone() {
        var info = clientInfo();
        $('who').textContent = state.meta.userName
            ? 'Signed in as ' + state.meta.userName + (state.meta.practice ? ', ' + state.meta.practice : '') + '.'
            : '';
        $('downloaded').textContent = state.meta.downloadedAt
            ? state.horses.length + ' horses, downloaded ' + timeText(state.meta.downloadedAt) + '.'
            : '';
        var rows = [
            ['App version', info.version],
            ['Installed on the home screen', info.standalone ? 'Yes' : 'No'],
            ['Opens with no signal', info.workerReady ? 'Yes' : 'Not yet: keep the app open with a signal for a moment'],
            ['Service worker', info.workerState],
            ['Sends while the app is closed', info.backgroundSync ? 'This browser can' : 'No: keep the app open to send'],
            ['Voice recording', info.canRecord ? 'Yes (' + (info.audioType || 'browser default') + ')' : 'No'],
            ['Opened in', info.openMs === null ? 'not known' : info.openMs + ' ms'],
            ['Notes on this phone', state.entries.length + ' (' + unsentEntries().length + ' not sent)'],
            ['Storage used', core.formatBytes(info.storageUsed)],
            ['Storage allowed', core.formatBytes(info.storageQuota)],
            ['Storage protected from clean-up', info.storagePersisted === null ? 'not known' : info.storagePersisted ? 'Yes' : 'No'],
            ['Last sync', timeText(state.meta.lastSyncAt)],
            ['Browser', info.userAgent]
        ];
        var nodes = [];
        rows.forEach(function (row) {
            nodes.push(el('dt', '', row[0]));
            nodes.push(el('dd', '', String(row[1])));
        });
        fill($('details'), nodes);
    }

    function refreshStorage() {
        if (!navigator.storage || !navigator.storage.estimate) return Promise.resolve();
        return navigator.storage.estimate().then(function (estimate) {
            state.storage = estimate;
            return navigator.storage.persisted ? navigator.storage.persisted() : null;
        }).then(function (persisted) {
            state.persisted = persisted;
            if (state.view === 'phone') renderPhone();
        }).catch(function () { /* details stay "not known" */ });
    }

    // Ask the browser not to clean this storage up. It may say no; the
    // Phone screen shows the answer. Asked on a tap, as some browsers require.
    function askToKeepStorage() {
        if (!navigator.storage || !navigator.storage.persist) return;
        navigator.storage.persist().then(refreshStorage).catch(function () {});
    }

    function showSignIn(visible) {
        $('sign-in').hidden = !visible;
        $('sign-in-link').href = config.loginUrl;
    }

    // What the server said about this login, kept for the screen: the
    // practice's name, and if notes are test notes (CVET_TEST_MODE).
    function rememberSession(data) {
        return setMeta('practice', data.practice || '').then(function () {
            return setMeta('testMode', data.test_mode === true);
        });
    }

    function otherPersonText() {
        return 'This phone holds unsent notes from ' + (state.meta.userName || 'another person') +
            '. Sign in as that person to send them.';
    }

    // ── Download the yard: horses and prices ─────────────────────────────

    function setHorses(horses) {
        state.horses = horses;
        state.horsesById = {};
        horses.forEach(function (horse) { state.horsesById[horse.id] = horse; });
    }

    // `quiet`: the refresh the app does by itself when it opens. It says
    // nothing unless it brought new data.
    function download(quiet) {
        var buttons = [$('download'), $('download-first')];
        buttons.forEach(function (node) { node.disabled = true; });
        if (!quiet) {
            say('Downloading the horse list…');
            askToKeepStorage();
        }
        return request(config.yardUrl, {}, 30000).then(function (result) {
            if (result.kind === 'offline') {
                if (!quiet) say('No signal. The horse list on this phone has not changed.');
                return;
            }
            if (result.kind === 'ok' && result.status === 403) {
                if (!quiet) {
                    say(result.data && result.data.error === 'no_yard_link'
                        ? 'The practice has no open link to this yard. Ask the yard administrator.'
                        : DENIED_TEXT);
                }
                return;
            }
            if (result.kind === 'signin' || result.status !== 200) {
                if (!quiet) {
                    say('Sign in again to download the horse list.', true);
                    showSignIn(true);
                }
                return;
            }
            var data = result.data;
            if (state.meta.userId && state.meta.userId !== data.user.id && unsentEntries().length) {
                if (!quiet) say(otherPersonText());
                return;
            }
            showSignIn(false);
            var prices = (data.prices || []).map(fromServerPrice);
            return replaceAll('horses', data.horses).then(function () {
                return replaceAll('prices', prices);
            }).then(function () {
                setHorses(data.horses);
                state.prices = prices;
                if (state.draft) state.draft.horse = state.horsesById[state.draft.horse.id] || state.draft.horse;
                return setMeta('downloadedAt', data.generated_at);
            }).then(function () {
                return setMeta('userId', data.user.id);
            }).then(function () {
                return setMeta('userName', data.user.name);
            }).then(function () {
                var yard = data.yard || {};
                return setMeta('yard', { name: yard.name || '', billTo: yard.bill_to || 'yard' });
            }).then(function () {
                return rememberSession(data);
            }).then(function () {
                if (!quiet) say('Horse list downloaded.');
            });
        }).catch(function (error) {
            say('The phone could not store the horse list: ' + error.message, true);
        }).then(function () {
            buttons.forEach(function (node) { node.disabled = false; });
            render();
            refreshStorage();
        });
    }

    // ── Sync ─────────────────────────────────────────────────────────────

    function sendEntry(entry, csrf) {
        var form = new FormData();
        form.append('client_id', entry.clientId);
        if (entry.visitId) form.append('visit_id', entry.visitId);
        form.append('horse_id', String(entry.horseId));
        form.append('horse_name', entry.horseName);
        form.append('note', entry.note);
        if (entry.followUp) form.append('follow_up_date', entry.followUp);
        if (entry.lines && entry.lines.length) {
            form.append('lines', JSON.stringify(entry.lines.map(function (line) {
                return {
                    id: line.id, price_id: line.priceId, description: line.description,
                    quantity: String(line.qty), unit_price: core.decimal(line.unitPence), vat_rate: line.vatRate
                };
            })));
        }
        form.append('device_created_at', entry.createdAt);
        form.append('attempts', String(entry.attempts));
        form.append('client_info', JSON.stringify(clientInfo()));
        if (entry.photo) {
            form.append('photo', new Blob([entry.photo.buffer], { type: entry.photo.type }), 'photo');
            form.append('photo_id', entry.photo.id);
            form.append('photo_sha256', entry.photo.sha256);
        }
        if (entry.audio) {
            form.append('audio', new Blob([entry.audio.buffer], { type: entry.audio.type }), 'audio');
            form.append('audio_id', entry.audio.id);
            form.append('audio_sha256', entry.audio.sha256);
        }
        return post(config.notesUrl, form, csrf, 120000);
    }

    // Tell the server about each finished visit, once every note of that
    // visit has arrived. Resolves to '' or to why it stopped.
    function sendFinishes(csrf) {
        var queue = finishQueue().slice();
        function next() {
            var item = queue.shift();
            if (!item) return Promise.resolve('');
            var waiting = state.entries.some(function (entry) {
                return entry.visitId === item.id && entry.status === 'pending';
            });
            if (waiting) return next();
            var form = new FormData();
            form.append('finished_at', item.finishedAt);
            return post(config.visitsUrl + item.id + '/finish/', form, csrf).then(function (result) {
                var what = result.kind === 'ok' ? core.outcome(result.status)
                    : result.kind === 'signin' ? 'signin' : 'later';
                // sent: the visit is closed. failed: the server has no
                // such visit (none of its notes arrived). Either way
                // there is nothing more to send for it.
                if (what !== 'sent' && what !== 'failed') return what;
                return setMeta('finishQueue', finishQueue().filter(function (left) {
                    return left.id !== item.id;
                })).then(next);
            });
        }
        return next();
    }

    // Send every unsent note, oldest first, one at a time, then every
    // finished visit. Stops at the first thing that cannot be sent now;
    // the rest wait for the next sync. `byHand` is true for a tap: it
    // also sends notes the server refused before, and it always says
    // what happened.
    function sync(byHand) {
        if (state.syncing) return Promise.resolve();
        var queue = unsentEntries().filter(function (entry) {
            return byHand || entry.status !== 'failed';
        });
        if (!queue.length && !finishQueue().length) {
            if (byHand) say('Nothing to send.');
            return Promise.resolve();
        }
        state.syncing = true;
        $('sync').disabled = true;
        if (byHand) say('Sending…');

        var sent = 0;
        var stop = '';

        function next(csrf) {
            var entry = queue.shift();
            if (!entry || stop) return Promise.resolve();
            entry.attempts += 1;
            return put('entries', entry).then(function () {
                return sendEntry(entry, csrf);
            }).then(function (result) {
                var what = result.kind === 'ok' ? core.outcome(result.status)
                    : result.kind === 'signin' ? 'signin' : 'later';
                // "Not found" for a note means C-vet is turned off on the
                // server. That can change: keep the note and try later.
                if (result.status === 404) what = 'later';
                if (what === 'sent') {
                    entry.status = 'sent';
                    entry.sentAt = new Date().toISOString();
                    entry.lastError = '';
                    // The server holds the files now. Free the space.
                    entry.photo = null;
                    entry.audio = null;
                    sent += 1;
                } else if (what === 'failed') {
                    entry.status = 'failed';
                    entry.lastError = (result.data && result.data.error) || 'refused';
                } else {
                    stop = what;
                }
                return put('entries', entry);
            }).then(function () {
                renderBar();
                return next(csrf);
            });
        }

        return getSession().then(function (result) {
            if (result.kind !== 'ok') {
                stop = result.kind === 'signin' || result.kind === 'denied' ? result.kind : 'later';
                return;
            }
            var session = result.session;
            if (state.meta.userId && state.meta.userId !== session.user.id) {
                stop = 'other';
                return;
            }
            showSignIn(false);
            return rememberSession(session).then(function () {
                return next(session.csrf);
            }).then(function () {
                if (stop) return;
                return sendFinishes(session.csrf).then(function (why) { stop = why; });
            });
        }).catch(function (error) {
            stop = 'storage:' + error.message;
        }).then(function () {
            state.syncing = false;
            $('sync').disabled = false;
            var left = unsentEntries().length;
            if (stop === 'signin') {
                say('Sign in again to send. Your notes are safe on this phone.', true);
                showSignIn(true);
            } else if (stop === 'denied') {
                say(DENIED_TEXT, true);
            } else if (stop === 'other') {
                say(otherPersonText(), true);
            } else if (stop === 'later') {
                if (byHand || sent) {
                    say((sent ? sent + ' sent. ' : '') +
                        'No signal for the rest. They stay on this phone and go when the signal is back.');
                }
            } else if (stop.indexOf('storage:') === 0) {
                say('The phone could not update its storage: ' + stop.slice(8), true);
            } else if (sent || byHand) {
                say((sent === 1 ? '1 note sent.' : sent + ' notes sent.') +
                    (left ? ' ' + left + ' could not be sent: open the horse to see why.' : ''));
            }
            if (sent) setMeta('lastSyncAt', new Date().toISOString());
            render();
            refreshStorage();
        });
    }

    // ── Sign out and clear ───────────────────────────────────────────────

    function forgetThisPhone() {
        setHorses([]);
        state.prices = [];
        state.entries = [];
        state.meta = {};
        state.draft = null;
        var jobs = [clearEverything()];
        // The stored page too, so the app does not open for the next
        // person who holds the phone.
        if (window.caches && caches.keys) {
            jobs.push(caches.keys().then(function (names) {
                return Promise.all(names.filter(function (name) {
                    return name.indexOf('cvet-') === 0;
                }).map(function (name) { return caches.delete(name); }));
            }));
        }
        if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
            jobs.push(navigator.serviceWorker.getRegistrations().then(function (registrations) {
                return Promise.all(registrations.map(function (registration) {
                    return registration.unregister();
                }));
            }));
        }
        return Promise.all(jobs);
    }

    function lossQuestion(action, plain) {
        var count = unsentEntries().length;
        return count
            ? core.unsentText(count) + '. ' + action + ' deletes ' + (count === 1 ? 'it' : 'them') + ' from this phone for good. Sync first if you can. Go on?'
            : plain;
    }

    // Sign out needs a signal: the server must end the sign-in. With no
    // signal, Clear this phone removes the data and the sign-in stays.
    function signOut() {
        if (!window.confirm(lossQuestion('Signing out', 'Sign out and remove the horse list and all notes from this phone?'))) return;
        say('Signing out…');
        getSession().then(function (result) {
            if (result.kind === 'offline') {
                say('No signal. Sign out needs a signal. To remove the data now, tap Clear this phone.');
                return;
            }
            var done = result.kind === 'ok' ? request(config.logoutUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': result.session.csrf },
                redirect: 'manual'
            }, 20000) : Promise.resolve();
            return done.then(forgetThisPhone).then(function () {
                window.location.href = config.loginUrl;
            });
        }).catch(function (error) {
            say('Sign out did not finish: ' + error.message);
        });
    }

    function clearPhone() {
        if (!window.confirm(lossQuestion('Clearing', 'Remove the horse list and all notes from this phone?'))) return;
        clearEverything().then(function () {
            setHorses([]);
            state.prices = [];
            state.entries = [];
            state.meta = {};
            state.draft = null;
            say('This phone is clear. You are still signed in.');
            show('horses');
            refreshStorage();
        });
    }

    // ── Start ────────────────────────────────────────────────────────────

    function registerWorker() {
        if (!('serviceWorker' in navigator)) {
            state.workerState = 'not supported';
            return;
        }
        navigator.serviceWorker.register(config.swUrl, { scope: config.scope }).then(function (registration) {
            var worker = registration.active || registration.waiting || registration.installing;
            state.workerState = worker ? worker.state : 'registered';
            // "ready" means an active worker holds the stored page. That
            // is what the next open needs. This page may not be under the
            // worker's control yet (it loaded while the worker was being
            // installed), and it does not have to be.
            navigator.serviceWorker.ready.then(function () {
                state.workerState = 'activated';
                state.workerReady = true;
                if (state.view === 'phone') renderPhone();
            });
        }).catch(function (error) {
            state.workerState = 'failed: ' + error.message;
        });
    }

    function start() {
        Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (tab) {
            tab.addEventListener('click', function () {
                if (state.view === 'horse' && !leaveHorse()) return;
                show(tab.dataset.view);
            });
        });
        $('unsent').addEventListener('click', function () { sync(true); });
        $('sync').addEventListener('click', function () { sync(true); });
        $('download').addEventListener('click', function () { download(false); });
        $('download-first').addEventListener('click', function () { download(false); });
        $('search').addEventListener('input', renderHorses);
        $('select-toggle').addEventListener('click', toggleSelecting);
        $('select-add').addEventListener('click', openPicker);
        $('picker-cancel').addEventListener('click', function () { $('picker').hidden = true; });
        $('back').addEventListener('click', function () { if (leaveHorse()) show('horses'); });
        $('follow-up').addEventListener('change', function () {
            if (!state.draft) return;
            state.draft.followUp = $('follow-up').value;
            renderFollowUps();
        });
        $('note').addEventListener('input', renderSuggestion);
        $('photo').addEventListener('change', onPhotoChosen);
        $('record').addEventListener('click', toggleRecording);
        $('save').addEventListener('click', saveDraft);
        $('load-history').addEventListener('click', loadOlderNotes);
        $('finish').addEventListener('click', finishVisit);
        $('price-form').addEventListener('submit', function (event) { savePrice(event, false); });
        $('price-hide').addEventListener('click', function () { savePrice(null, true); });
        $('price-cancel').addEventListener('click', function () { editPrice(null); });
        $('clear').addEventListener('click', clearPhone);
        $('sign-out').addEventListener('click', signOut);

        window.addEventListener('online', function () { renderBar(); sync(false); });
        window.addEventListener('offline', renderBar);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') sync(false);
        });
        // The phone cannot send while the app is closed (no background
        // sync on iPhone), so it tries each minute while the app is open.
        setInterval(function () { sync(false); }, AUTO_SYNC_MS);

        registerWorker();

        Promise.all(STORES.map(getAll)).then(function (stored) {
            stored[0].forEach(function (row) { state.meta[row.key] = row.value; });
            setHorses(stored[1]);
            state.prices = stored[2].sort(function (a, b) { return a.name.localeCompare(b.name); });
            state.entries = stored[3];
            show('horses');
            // The speed measure: time since the page started to load.
            // The plan's limit is 1 second (docs/C_VET_PLAN.md, section 5).
            if (window.performance && performance.now) state.openMs = Math.round(performance.now());
            refreshStorage();
            sync(false);
            // Fresh due dates and prices for today's visit, if the list
            // is from another day. Behind the screen: nothing waits for it.
            var age = state.meta.downloadedAt ? Date.now() - new Date(state.meta.downloadedAt).getTime() : 0;
            if (age > REFRESH_AFTER_MS && navigator.onLine) download(true);
        }).catch(function (error) {
            say('This browser cannot store data for C-vet: ' + error.message, true);
            render();
        });
    }

    if (!window.indexedDB) {
        say('This browser cannot store data for C-vet.', true);
    } else {
        start();
    }
})();
