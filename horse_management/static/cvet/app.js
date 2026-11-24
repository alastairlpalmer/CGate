/* C-vet: the page (docs/C_VET_PLAN.md, sections 4, 5 and 7c).

   A web app that holds the practice's clients, their horses, the price
   list and the vet's notes on the phone, with or without a signal, and
   sends each note to the practice's records exactly once.

   The vet works client first: Clients, then one client's horses, then
   one horse. Visits is the record: the open visits to finish, and each
   visit that was made, with its notes, photos and recordings.

   Where things are kept:
     - the page and its files: the service worker's store (templates/cvet/sw.js)
     - clients, horses, prices, notes and the visits: IndexedDB, below
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
        view: 'clients',
        clients: [],
        clientsById: {},
        client: '',        // the client whose horses are on the screen
        horses: [],
        horsesById: {},
        prices: [],
        entries: [],
        // downloadedAt, userId, userName, practice, testMode, yard, visits,
        // closed, visitList, finishQueue, lastSyncAt, draft
        meta: {},
        draft: null,       // the horse screen: { horse, lines, followUp, photo, audio }
        picker: null,      // the item list, while it is open: { mode, category }
        open: {},          // the parts of the Visits screen that are unfolded
        visitDetails: {},  // visit id -> the practice's record of it, once loaded
        visitTried: {},
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

    var STORES = ['meta', 'horses', 'prices', 'entries', 'clients'];
    var dbPromise = null;

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise(function (resolve, reject) {
            var request = indexedDB.open('cvet', 3);
            request.onupgradeneeded = function () {
                var db = request.result;
                var keys = { meta: 'key', horses: 'id', prices: 'id', entries: 'clientId', clients: 'id' };
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
            state.netDown = result.kind === 'offline';
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
        var node = $('sync-message');
        node.textContent = text;
        // "Willow saved." floats over the page and goes: it moves nothing.
        node.classList.toggle('toast', !stays);
        if (sayTimer) clearTimeout(sayTimer);
        sayTimer = text && !stays ? setTimeout(function () { node.textContent = ''; }, 5000) : null;
    }

    function unsentEntries() { return core.unsent(state.entries); }
    function finishQueue() { return state.meta.finishQueue || []; }

    // ── Clients, and the visit of each one ───────────────────────────────

    // A phone that holds horses from before C-vet had a client list: they
    // are the horses of the C-gate yard, under this id until the next
    // download brings the yard as a client.
    var OLD_YARD = 'yard';

    function yardName() { return (state.meta.yard && state.meta.yard.name) || 'The yard'; }

    function setClients(clients) {
        if (!clients.length && state.horses.length) {
            clients = [{ id: OLD_YARD, name: yardName(), kind: 'yard', is_yard: true, is_active: true }];
        }
        state.clients = clients.sort(function (a, b) { return a.name.localeCompare(b.name); });
        state.clientsById = {};
        state.clients.forEach(function (client) { state.clientsById[client.id] = client; });
    }

    function yardClientId() {
        var yard = state.clients.filter(function (client) { return client.is_yard; })[0];
        return yard ? yard.id : OLD_YARD;
    }

    function clientById(id) { return state.clientsById[id] || null; }
    function clientIdOf(horse) { return horse.client_id || yardClientId(); }

    // The horses of one client that are in use (not archived).
    function clientHorses(clientId) {
        return state.horses.filter(function (horse) {
            return clientIdOf(horse) === clientId && horse.is_active !== false;
        });
    }

    // The visit of each client that is open now, or was finished last:
    // { client id: { id, day, finished } }. One visit for each client
    // for each day.
    function visits() { return state.meta.visits || {}; }
    // What the phone knows about each visit it finished:
    // { visit id: { finishedAt, bills, billWhen } }.
    function closed() { return state.meta.closed || {}; }

    function entriesOf(visitId) {
        return state.entries.filter(function (entry) { return entry.visitId === visitId; });
    }

    // The entries of the client's open visit: empty when there is none.
    function openEntries(clientId) {
        var visit = visits()[clientId];
        return visit && !visitDone(visit) ? entriesOf(visit.id) : [];
    }

    // Finished on this phone, or on another one: the practice's list of
    // visits says so (the vet finished it at the computer).
    function visitDone(visit) {
        return !!visit.finished || !!closed()[visit.id] || (state.meta.visitList || []).some(function (listed) {
            return listed.id === visit.id && listed.status === 'finished';
        });
    }

    function keepVisit(clientId, visit) {
        var all = Object.assign({}, visits());
        all[clientId] = visit;
        return setMeta('visits', all);
    }

    function noteView(entry) { return core.noteFromEntry(entry, config.filesUrl); }

    // Who the bill for a visit goes to: 'yard' (the client; the C-gate
    // yard then invoices its owners) or 'owners' (the C-gate yard only).
    function billTo(clientId) {
        var client = clientById(clientId);
        if (!client || !client.is_yard) return 'yard';
        return (state.meta.yard && state.meta.yard.billTo) || client.bill_to || 'yard';
    }

    function payersText(horse) {
        // A horse of the practice's own client: the owner is a name only.
        if (horse.own) return horse.owner ? 'Owner: ' + horse.owner : '';
        return core.ownersText(horse.payers, billTo(clientIdOf(horse)));
    }

    function dueText(due) {
        var when = core.shortDate(due.date, today());
        return due.label + (due.status === 'overdue' ? ' overdue ' : ' due ') + when;
    }

    function countText(count, one, many) { return count === 1 ? '1 ' + one : count + ' ' + many; }

    // ── The bar at the top, and the tabs ─────────────────────────────────

    // Clients and horses made or changed on the phone and not sent yet.
    function pendingChanges() {
        return state.clients.concat(state.horses).filter(function (item) { return item.unsent; });
    }

    function renderBar() {
        // The chip shows only when there is no signal: that is the thing
        // to know. navigator.onLine can say "online" on a network with no
        // way out, so a request that got no answer counts too.
        var chip = $('signal');
        chip.hidden = navigator.onLine && !state.netDown;

        var count = unsentEntries().length;
        var finishes = finishQueue().length;
        var changes = pendingChanges().length;
        var node = $('unsent');
        node.textContent = core.unsentText(count, finishes, changes);
        node.className = 'chip count ' + (count || finishes || changes ? 'pending' : 'clear');
        // Said on the screen for as long as it is true: these notes are
        // test notes, not clinical records.
        $('test-mode').hidden = state.meta.testMode !== true;

        // The visits that wait for Finish visit.
        var open = Object.keys(visits()).filter(function (clientId) {
            return openEntries(clientId).length > 0;
        }).length;
        var badge = $('visit-badge');
        badge.hidden = open === 0;
        badge.textContent = String(open);
    }

    var VIEWS = ['clients', 'client', 'horse', 'visits', 'bills', 'prices', 'phone'];

    // A computer, or a tablet on its side: the screen has room for two
    // parts. The clients stay on the left while one client, or one
    // horse, is on the right (static/cvet/app.css, "A wide screen").
    var wideScreen = typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 1000px)') : null;
    function isWide() { return !!(wideScreen && wideScreen.matches); }
    function besideClients(view) { return isWide() && view === 'client'; }

    // What only a wide screen needs is in files of its own (desk.css,
    // desk.js): the layout, the keyboard, the price table, the client's
    // page and print. Loaded once, the first time the screen is wide.
    var deskLoaded = false;
    function loadDesk() {
        if (deskLoaded || !isWide()) return;
        deskLoaded = true;
        var css = el('link');
        css.rel = 'stylesheet';
        css.href = config.deskCss;
        document.head.appendChild(css);
        var script = el('script');
        script.src = config.deskJs;
        document.head.appendChild(script);
    }
    // Tell desk.js that a part of the screen was drawn. Nothing on a phone.
    function desk(what, a, b) { if (window.CvetDesk) window.CvetDesk(what, a, b); }

    function show(view) {
        if (isWide() && view === 'clients') {
            // The right part is never empty: the client that was open, or the first one.
            var kept = clientById(state.client);
            var pick = kept && kept.is_active !== false ? kept : state.clients.filter(function (client) {
                return client.is_active !== false;
            })[0];
            if (pick) {
                state.client = pick.id;
                view = 'client';
            }
        }
        if (state.view === 'horse' && view !== 'horse') stopRecording();
        if (state.view !== view) freeMedia();
        state.view = view;
        VIEWS.forEach(function (name) {
            $('view-' + name).hidden = name !== view && !(name === 'clients' && besideClients(view));
        });
        document.body.classList.toggle('split', besideClients(view));
        Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (tab) {
            // A client and a horse are under Clients.
            var current = tab.dataset.view === view ||
                ((view === 'client' || view === 'horse') && tab.dataset.view === 'clients');
            if (current) tab.setAttribute('aria-current', 'page');
            else tab.removeAttribute('aria-current');
        });
        $('select-bar').hidden = !(view === 'client' && state.selecting);
        render();
        window.scrollTo(0, 0);
        if (view === 'visits') loadVisitList();
        if (view === 'bills') loadBills();
    }

    function render() {
        // A wide screen with clients: `show` puts one of them on the right.
        if (state.view === 'clients' && isWide() && state.clients.some(function (client) {
            return client.is_active !== false;
        })) { show('clients'); return; }
        renderBar();
        if (besideClients(state.view)) renderClients();
        if (state.view === 'clients') renderClients();
        else if (state.view === 'client') renderClient();
        else if (state.view === 'horse') renderHorse();
        else if (state.view === 'visits') renderVisits();
        else if (state.view === 'bills') renderBills();
        else if (state.view === 'prices') renderPrices();
        else if (state.view === 'phone') renderPhone();
        desk('view');
    }

    // ── Clients ──────────────────────────────────────────────────────────

    function clientRow(client) {
        var item = el('li');
        var row = button('horse go', undefined, function () { openClient(client.id); });
        // On a wide screen: which client is on the right.
        if (client.id === state.client && besideClients(state.view)) row.setAttribute('aria-current', 'true');
        row.appendChild(el('span', 'initial', (client.name || '?').charAt(0).toUpperCase()));
        var text = el('span', 'text');
        text.appendChild(el('span', 'strong', client.name));
        var horses = clientHorses(client.id);
        var owed = sumOf(bills().filter(function (bill) { return bill.client_id === client.id && !bill.paid_on; }));
        text.appendChild(el('small', '', [
            core.kindLabel(client.kind), countText(horses.length, 'horse', 'horses'),
            client.unsent ? 'not sent yet' : client.refused ? 'not saved' : '',
            owed ? core.money(owed) + ' to pay' : ''
        ].filter(Boolean).join(' · ')));
        row.appendChild(text);
        var day = today();
        var due = horses.filter(function (horse) { return core.horseDue(horse, day); }).length;
        if (openEntries(client.id).length) row.appendChild(el('span', 'pill wait', 'Visit open'));
        else if (due) row.appendChild(el('span', 'due soon', due + ' due'));
        item.appendChild(row);
        return item;
    }

    // One horse in a list. options: selecting, picked, seen (a tick),
    // client (name its client), ids (show chip and passport), onClick.
    function horseRow(horse, options) {
        options = options || {};
        var item = el('li');
        var row = button('horse' + (options.selecting ? '' : ' go'), undefined, options.onClick || function () {
            if (state.view === 'horse' && state.draft && state.draft.horse !== horse && !leaveHorse()) return;
            openHorse(horse);
        });
        if (options.selecting) {
            row.appendChild(el('span', 'mark' + (options.picked ? ' picked' : ''), options.picked ? '✓' : ''));
            row.setAttribute('aria-pressed', options.picked ? 'true' : 'false');
        } else if (options.seen) {
            row.appendChild(el('span', 'mark done', '✓'));
        }
        var text = el('span', 'text');
        text.appendChild(el('span', 'strong', horse.name));
        var client = options.client ? clientById(clientIdOf(horse)) : null;
        var line = [
            client ? client.name : '', horse.owner, horse.location,
            horse.unsent ? 'not sent yet' : horse.refused ? 'not saved' : ''
        ].filter(Boolean).join(' · ');
        if (line) text.appendChild(el('small', '', line));
        // Chip and passport numbers take a line each horse. Show
        // them only while the vet is searching, to check a match.
        var ids = options.ids ? [
            horse.microchip ? 'Chip ' + horse.microchip : '',
            horse.passport ? 'Passport ' + horse.passport : ''
        ].filter(Boolean).join(' · ') : '';
        if (ids) text.appendChild(el('small', '', ids));
        row.appendChild(text);
        var due = core.horseDue(horse, today());
        if (due) row.appendChild(el('span', 'due ' + due.status, dueText(due)));
        item.appendChild(row);
        return item;
    }

    function renderClients() {
        var query = $('client-search').value.trim();
        var shown = state.clients.filter(function (client) {
            return client.is_active !== false && core.matchesClient(client, query);
        });
        var archived = state.clients.filter(function (client) { return client.is_active === false; });
        fill($('clients'), shown.map(clientRow));
        // A vet may know the horse and not the client: a search finds both.
        var found = query ? core.sortHorses(state.horses, today()).filter(function (horse) {
            var client = clientById(clientIdOf(horse));
            return horse.is_active !== false && client && client.is_active !== false && core.matchesHorse(horse, query);
        }).slice(0, 30) : [];
        // What is due, at every client: one tap on the count shows the horses.
        var day = today();
        var due = core.sortHorses(state.horses, day).filter(function (horse) {
            var client = clientById(clientIdOf(horse));
            return horse.is_active !== false && client && client.is_active !== false && core.horseDue(horse, day);
        });
        if (!due.length) state.dueOnly = false;
        var open = Object.keys(visits()).filter(function (clientId) { return openEntries(clientId).length > 0; }).length;
        function count(number, one, many, hot, onClick) {
            var node = onClick ? button('count', undefined, onClick) : el('span', 'count');
            node.appendChild(el('b', '', String(number)));
            node.appendChild(document.createTextNode(' ' + (number === 1 ? one : many)));
            if (hot) node.classList.add('hot');
            return node;
        }
        var counts = [count(shown.length, 'client', 'clients')];
        if (due.length) {
            var dueCount = count(due.length, 'horse due', 'horses due', true, function () {
                state.dueOnly = !state.dueOnly;
                renderClients();
            });
            dueCount.setAttribute('aria-pressed', state.dueOnly ? 'true' : 'false');
            counts.push(dueCount);
        }
        if (open) {
            counts.push(count(open, 'visit open', 'visits open', true, function () {
                show('visits');
            }));
        }
        // What every client together still has to pay: a tap goes to Invoices.
        var owed = sumOf(bills().filter(function (bill) { return !bill.paid_on; }));
        if (owed) {
            var owedCount = button('count', undefined, function () {
                state.billFilter = 'unpaid';
                show('bills');
            });
            owedCount.appendChild(el('b', '', core.money(owed)));
            owedCount.appendChild(document.createTextNode(' to pay'));
            counts.push(owedCount);
        }
        fill($('counts'), state.clients.length && !query ? counts : []);
        if (state.dueOnly && !query) found = due;
        $('found-title').textContent = state.dueOnly && !query ? 'Due' : 'Horses';
        fill($('found-horses'), found.map(function (horse) { return horseRow(horse, { client: true, ids: !!query }); }));
        $('found-horses-box').hidden = found.length === 0;
        $('no-match').hidden = !(query && !shown.length && !found.length);
        fill($('archived-clients'), archived.map(clientRow));
        $('archived-clients-box').hidden = archived.length === 0 || !!query;
        var none = state.clients.length === 0;
        $('no-clients').hidden = !none;
        $('client-search').hidden = none;
    }

    // ── One client: its horses ───────────────────────────────────────────

    function openClient(id) {
        // On a wide screen the clients are in reach while a note is written.
        if (state.view === 'horse' && !leaveHorse()) return;
        state.client = id;
        state.selecting = false;
        state.selected = {};
        state.clientTab = 'horses';
        $('search').value = '';
        show('client');
    }

    function renderClient() {
        var client = clientById(state.client);
        if (!client) { show('clients'); return; }
        var day = today();
        var horses = clientHorses(client.id);
        $('client-name').textContent = client.name;
        $('client-line').textContent = [
            core.kindLabel(client.kind), countText(horses.length, 'horse', 'horses'),
            client.is_active === false ? 'archived' : ''
        ].filter(Boolean).join(' · ');
        // Who to ask for, and one tap to call, to write or to find the way.
        var contact = [];
        function reach(text, href) {
            var link = el('a', '', text);
            link.href = href;
            if (/^https/.test(href)) { link.target = '_blank'; link.rel = 'noopener'; }
            contact.push(link);
        }
        if (client.contact_name) contact.push(el('span', '', client.contact_name));
        if (client.phone) reach(client.phone, 'tel:' + client.phone.replace(/[^+\d]/g, ''));
        if (client.email) reach(client.email, 'mailto:' + client.email);
        if (client.address) reach('Directions', 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(client.address));
        fill($('client-contact'), contact);
        // The C-gate yard: its name and its horses are the yard's records.
        $('client-edit').hidden = !!client.is_yard;
        $('horse-add').hidden = !!client.is_yard;

        // The open visit, in one line: a tap goes to its summary.
        var entries = openEntries(client.id);
        var groups = core.groupNotes(entries.map(noteView));
        var banner = $('client-visit');
        banner.hidden = entries.length === 0;
        banner.textContent = 'Visit open · ' + countText(groups.length, 'horse', 'horses') + ' · ' +
            core.money(groups.reduce(function (sum, group) { return sum + group.total; }, 0));

        // Three ways to look at one client. The number on Invoices: not paid yet.
        var tab = state.clientTab || 'horses';
        var unpaid = bills().filter(function (bill) { return bill.client_id === client.id && !bill.paid_on; }).length;
        fill($('client-tabs'), [
            ['horses', 'Horses', String(horses.length)], ['timeline', 'Timeline', ''],
            ['bills', 'Invoices', unpaid ? String(unpaid) : '']
        ].map(function (choice) {
            var node = button('seg-tab' + (tab === choice[0] ? ' on' : ''), choice[1], function () {
                state.clientTab = choice[0];
                state.selecting = false;
                state.selected = {};
                renderClient();
                if (choice[0] !== 'horses') { loadVisitList(); loadBills(); }
            });
            node.setAttribute('role', 'tab');
            node.setAttribute('aria-selected', tab === choice[0] ? 'true' : 'false');
            if (choice[2]) node.appendChild(el('span', 'n' + (choice[0] === 'bills' ? ' hot' : ''), choice[2]));
            return node;
        }));
        $('client-horses').hidden = tab !== 'horses';
        $('client-timeline').hidden = tab !== 'timeline';
        $('client-bills').hidden = tab !== 'bills';
        fill($('client-timeline'), tab === 'timeline' ? clientTimeline(client, horses) : []);
        fill($('client-bills'), tab === 'bills' ? clientBills(client) : []);

        var seen = {};
        entries.forEach(function (entry) { seen[entry.horseId] = true; });
        var query = $('search').value;
        fill($('horses'), core.sortHorses(horses, day).filter(function (horse) {
            return core.matchesHorse(horse, query);
        }).map(function (horse) {
            return horseRow(horse, {
                selecting: state.selecting, picked: !!state.selected[horse.id], seen: seen[horse.id], ids: !!query,
                onClick: state.selecting ? function () { toggleSelected(horse); } : null
            });
        }));
        $('no-horses').hidden = horses.length > 0;
        $('no-horses').textContent = client.is_yard
            ? 'No horses. The horses of this client come from C-gate.'
            : 'No horses yet. Tap Add horse.';
        $('search').parentNode.hidden = horses.length === 0;

        var archived = state.horses.filter(function (horse) {
            return clientIdOf(horse) === client.id && horse.is_active === false;
        });
        fill($('archived-horses'), archived.map(function (horse) {
            return horseRow(horse, { onClick: function () { editHorse(horse); } });
        }));
        $('archived-horses-box').hidden = archived.length === 0;

        var toggle = $('select-toggle');
        toggle.textContent = state.selecting ? 'Done' : 'Select';
        toggle.className = 'button slim' + (state.selecting ? ' on' : '');
        var picked = Object.keys(state.selected).length;
        $('select-bar').hidden = !state.selecting;
        // Room under the list for the bar, so it hides no horse.
        $('view-client').classList.toggle('selecting', state.selecting);
        $('select-count').textContent = countText(picked, 'horse', 'horses') + ' selected';
        $('select-add').disabled = picked === 0;
    }

    function toggleSelected(horse) {
        if (state.selected[horse.id]) delete state.selected[horse.id];
        else state.selected[horse.id] = true;
        renderClient();
    }

    function toggleSelecting() {
        state.selecting = !state.selecting;
        state.selected = {};
        renderClient();
    }

    // ── The item list ────────────────────────────────────────────────────
    //
    // A search, the categories, and the prices under them. Two uses:
    //   'draft'     items for the note being written. The list stays
    //               open, so several items take one visit to it.
    //   'selected'  one item, added to every selected horse.

    function openPicker(mode) {
        state.picker = { mode: mode, category: '' };
        $('picker-search').value = '';
        var picked = Object.keys(state.selected).length;
        $('picker-title').textContent = mode === 'draft' ? 'Add item'
            : 'Add item to ' + countText(picked, 'horse', 'horses');
        $('picker-cancel').textContent = mode === 'draft' ? 'Done' : 'Cancel';
        $('picker-other').hidden = mode !== 'draft';
        $('picker').hidden = false;
        renderPicker();
        $('picker-list').scrollTop = 0;
        // With a keyboard at hand, the search is ready. On a phone it
        // would bring the keyboard up over the list.
        if (isWide()) $('picker-search').focus();
    }

    function closePicker() {
        $('picker').hidden = true;
        state.picker = null;
    }

    function renderPicker() {
        var picker = state.picker;
        if (!picker) return;
        var categories = core.priceCategories(state.prices);
        fill($('picker-categories'), categories.length > 1 ? [''].concat(categories).map(function (name) {
            var chip = button('tile' + (picker.category === name ? ' on' : ''), name || 'All', function () {
                picker.category = name;
                renderPicker();
            });
            chip.setAttribute('aria-pressed', picker.category === name ? 'true' : 'false');
            return chip;
        }) : []);
        var groups = core.groupPrices(state.prices, $('picker-search').value, picker.category);
        if (!$('picker-search').value.trim() && !picker.category) {
            var often = core.oftenUsed(state.prices, state.entries);
            if (often.length) groups.unshift({ category: 'Often used', items: often });
        }
        var nodes = [];
        groups.forEach(function (group) {
            if (categories.length > 1 || group.category === 'Often used') nodes.push(el('h3', '', group.category));
            group.items.forEach(function (price) {
                var row = button('pick', undefined, function () {
                    if (picker.mode === 'draft') { addLine(lineFrom(price)); renderPicker(); }
                    else addToSelected(price);
                });
                row.appendChild(el('span', 'text', price.name));
                // How many of it are on the note already.
                var qty = picker.mode === 'draft' && state.draft ? state.draft.lines.reduce(function (sum, line) {
                    return sum + (line.priceId === price.id ? line.qty : 0);
                }, 0) : 0;
                if (qty) row.appendChild(el('span', 'count', '× ' + qty));
                row.appendChild(el('span', 'amount', core.money(price.pence)));
                nodes.push(row);
            });
        });
        fill($('picker-list'), nodes);
        $('picker-empty').hidden = groups.length > 0;
        $('picker-empty').textContent = state.prices.some(function (price) { return price.isActive; })
            ? 'No item has these words.' : 'No prices yet. Add them on the Prices screen.';
    }

    // Several horses at once: one item, added to every selected horse.
    function addToSelected(price) {
        var horses = Object.keys(state.selected).map(function (id) { return state.horsesById[id]; }).filter(Boolean);
        var clientId = state.client;
        closePicker();
        if (!horses.length) return;
        var now = new Date();
        var visit = currentVisit(clientId, now);
        var entries = horses.map(function (horse) {
            return newEntry(horse, visit, now, { lines: [lineFrom(price)] });
        });
        keepVisit(clientId, visit).then(function () {
            return putAll('entries', entries);
        }).then(function () {
            Array.prototype.push.apply(state.entries, entries);
            state.selecting = false;
            state.selected = {};
            say(price.name + ' added to ' + countText(horses.length, 'horse', 'horses') + '.');
            render();
            sync(false);
        }).catch(function (error) {
            say('The phone could not save this: ' + error.message);
        });
    }

    // ── One horse ────────────────────────────────────────────────────────

    function lineFrom(price) {
        return {
            id: newId(), priceId: price.id, description: price.name,
            qty: 1, unitPence: price.pence, vatRate: price.vatRate
        };
    }

    // `saved`: a note that was being written when the app was closed or
    // reloaded (an iPhone can reload the app after the camera is used).
    function openHorse(horse, saved) {
        stopRecording();
        saved = saved || {};
        state.draft = {
            horse: horse, lines: saved.lines || [], followUp: saved.followUp || '',
            photo: saved.photo || null, audio: saved.audio || null
        };
        $('note').value = saved.note || '';
        $('photo').value = '';
        $('entry-message').textContent = '';
        $('history-message').textContent = '';
        state.olderNotes = null;
        state.lastNotes = null;
        // Where Back goes: the horses of this horse's client.
        state.client = clientIdOf(horse);
        showAttachments();
        show('horse');
        // The practice's record of this horse, behind the screen.
        if (navigator.onLine && !horse.unsent) loadOlderNotes(true);
    }

    // The photo and the recording of the note being written: what is
    // there, and a way to take each one off again.
    function showAttachments() {
        var draft = state.draft;
        function line(node, stored, text, kind) {
            node.textContent = '';
            if (!stored) return;
            node.appendChild(document.createTextNode(text + ' '));
            node.appendChild(button('link', 'Remove', function () {
                draft[kind] = null;
                if (kind === 'photo') $('photo').value = '';
                showAttachments();
                persistDraft();
            }));
        }
        line($('photo-state'), draft && draft.photo, draft && draft.photo ? 'Photo ready (' + core.formatBytes(draft.photo.bytes) + ').' : '', 'photo');
        line($('audio-state'), draft && draft.audio, draft && draft.audio ? 'Voice recorded (' + core.formatBytes(draft.audio.bytes) + ').' : '', 'audio');
        setPreview(draft ? draft.audio : null);
    }

    // Keep the note being written on the phone as it is typed, so that
    // closing the app, or the phone reloading it, loses nothing.
    var draftTimer = null;
    function persistDraft() {
        if (draftTimer) clearTimeout(draftTimer);
        draftTimer = setTimeout(function () {
            var draft = state.draft;
            var saved = draft && !draftIsEmpty() ? {
                horseId: draft.horse.id, note: $('note').value, lines: draft.lines,
                followUp: draft.followUp, photo: draft.photo, audio: draft.audio
            } : null;
            setMeta('draft', saved).catch(function () { /* the note is still on the screen */ });
        }, 300);
    }

    function forgetDraft() {
        if (draftTimer) clearTimeout(draftTimer);
        return setMeta('draft', null);
    }

    // The note box is as high as the note: no small box to scroll in.
    function growNote() {
        var box = $('note');
        box.style.height = 'auto';
        box.style.height = Math.max(box.scrollHeight + 2, 76) + 'px';
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
        forgetDraft();
        return true;
    }

    function renderHorse() {
        var draft = state.draft;
        if (!draft) { show(clientById(state.client) ? 'client' : 'clients'); return; }
        var horse = draft.horse;
        var day = today();
        var client = clientById(clientIdOf(horse));
        $('back').textContent = '‹ ' + (client ? client.name : 'Clients');
        $('horse-edit').hidden = !horse.own;
        $('horse-name').textContent = horse.name;
        $('horse-ids').textContent = [
            horse.about, horse.location,
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

        renderLines();
        renderFollowUps();
        renderHistory();
        growNote();
    }

    function addLine(line) {
        var same = !line.custom && state.draft.lines.filter(function (existing) {
            return existing.priceId === line.priceId && existing.unitPence === line.unitPence;
        })[0];
        if (same) same.qty += 1;       // a second tap on a tile: two of them
        else state.draft.lines.push(line);
        renderLines();
        persistDraft();
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
                name.addEventListener('input', function () { line.description = name.value; persistDraft(); });
                item.appendChild(name);
            } else {
                item.appendChild(el('span', 'strong', line.description));
            }
            var controls = el('div', 'line-controls');
            var total = el('span', 'line-total', core.money(core.lineTotal(line)));
            // At 1, one less is none: the button shows that it takes the item off.
            var less = button('step' + (line.qty > 1 ? '' : ' off'), line.qty > 1 ? '−' : '×', function () {
                if (line.qty > 1) line.qty -= 1;
                else draft.lines.splice(draft.lines.indexOf(line), 1);
                renderLines();
                persistDraft();
            });
            less.setAttribute('aria-label', line.qty > 1 ? 'One less' : 'Take this item off');
            controls.appendChild(less);
            controls.appendChild(el('span', 'qty', String(line.qty)));
            var more = button('step', '+', function () { line.qty += 1; renderLines(); persistDraft(); });
            more.setAttribute('aria-label', 'One more');
            controls.appendChild(more);
            var price = el('input');
            price.type = 'text';
            price.inputMode = 'decimal';
            // A price that is not a number stays in the box as typed,
            // marked, until it is put right.
            price.value = line.badPrice ? line.typed : core.decimal(line.unitPence);
            if (line.badPrice) price.className = 'bad';
            price.setAttribute('aria-label', 'Price for ' + (line.description || 'item'));
            // The price can be changed for this horse. The total follows
            // as the vet types; a price that is not a number is marked
            // and stops Save.
            price.addEventListener('input', function () {
                var pence = core.toPence(price.value);
                line.badPrice = pence === null;
                line.typed = price.value;
                price.className = line.badPrice ? 'bad' : '';
                if (!line.badPrice) line.unitPence = pence;
                total.textContent = core.money(core.lineTotal(line));
                renderLinesTotal();
                persistDraft();
            });
            controls.appendChild(price);
            controls.appendChild(total);
            item.appendChild(controls);
            return item;
        }));
        $('no-lines').hidden = draft.lines.length > 0;
        renderLinesTotal();
        renderRepeat();
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
            persistDraft();
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
                persistDraft();
            });
            tile.setAttribute('aria-pressed', draft.followUp === date ? 'true' : 'false');
            return tile;
        }));
        $('follow-up').value = draft.followUp;
        // The date in words, with how far off it is: a date box alone is easy to misread.
        var away = draft.followUp ? core.daysBetween(day, draft.followUp) : 0;
        $('follow-up-text').textContent = !draft.followUp ? 'No recheck.'
            : 'Recheck on ' + core.dayLabel(draft.followUp, day).replace(/^Today$/, 'today') +
              (away > 1 ? ', in ' + away + ' days.' : away < 0 ? ', which is in the past.' : '.');
        renderSuggestion();
    }

    // The notes of this horse: the ones on this phone, and the practice's
    // record of it once that is loaded. A sent note is read from the
    // record, which has its photo and its recording.
    function renderHistory() {
        var horse = state.draft.horse;
        var record = {};
        (state.olderNotes || []).forEach(function (note) { record[note.id] = note; });
        var notes = state.entries.filter(function (entry) {
            return String(entry.horseId) === String(horse.id);
        }).map(function (entry) {
            var sent = entry.status === 'sent' && record[entry.clientId];
            delete record[entry.clientId];
            return sent ? core.noteFromServer(sent) : noteView(entry);
        });
        Object.keys(record).forEach(function (id) { notes.push(core.noteFromServer(record[id])); });
        notes.sort(function (a, b) { return a.when < b.when ? 1 : -1; });
        // A timeline: the notes of a day under its heading, the newest day first.
        var box = el('div', 'tl');
        core.byDay(notes).forEach(function (group) {
            box.appendChild(el('p', 'day', core.dayLabel(group.day, today())));
            var card = el('div', 'card timeline');
            group.rows.forEach(function (note) { card.appendChild(noteCard(note)); });
            box.appendChild(card);
        });
        fill($('history'), notes.length ? [box] : []);
        $('no-history').hidden = notes.length > 0;
        state.lastNotes = notes;
        renderRepeat();
        // The last time, at the top of the screen: the history itself is under the note.
        var seen = notes[0];
        $('horse-last').hidden = !seen;
        if (seen) {
            $('horse-last').textContent = 'Last seen ' + core.dayLabel(seen.day, today()).replace(/^T/, 't').replace(/^Y/, 'y') +
                (seen.lines.length ? ': ' + seen.lines.map(function (line) { return line.description; }).join(', ') : '') + ' ›';
        }
        $('load-history').hidden = !!state.olderNotes;
        desk('history', horse, notes.length ? box : null);
    }

    // "Same items as 24 Nov: Sedation, Flu/Tet booster": one tap puts the
    // items of the horse's last note on this one, at today's prices. It
    // is offered while the note has no items; the vet taps to use it.
    function renderRepeat() {
        var node = $('repeat');
        var draft = state.draft;
        var last = (state.lastNotes || []).filter(function (note) { return note.lines.length; })[0];
        if (!draft || !last || draft.lines.length) { node.hidden = true; return; }
        node.hidden = false;
        node.textContent = 'Same items as ' + core.shortDate(last.day, today()) + ': ' +
            last.lines.map(function (line) { return line.description; }).join(', ');
        node.onclick = function () {
            last.lines.forEach(function (line) {
                var qty = line.qty || 1;
                var price = state.prices.filter(function (item) { return item.isActive && item.name === line.description; })[0];
                draft.lines.push(price ? Object.assign(lineFrom(price), { qty: qty }) : {
                    id: newId(), priceId: '', description: line.description, qty: qty,
                    unitPence: Math.round(line.pence / qty), vatRate: line.vatRate || '20.00', custom: true
                });
            });
            renderLines();
            persistDraft();
        };
    }

    // One note, read back: when, by whom, the items, the words, the
    // recheck date, and the photo and the recording to open.
    function noteCard(note) {
        var item = el('div', 'entry');
        var label = note.status === 'sent' ? (note.author || 'Sent')
            : note.status === 'failed' ? 'Not sent: ' + (note.error || 'the server refused it')
            : 'Not sent yet';
        // The time only: the day is the heading over the note, or the day of the visit.
        var time = new Date(note.when).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
        item.appendChild(el('small', 'state ' + note.status, time + ' · ' + label));
        note.lines.forEach(function (line) {
            var row = el('div', 'item');
            row.appendChild(el('span', '', (line.qty > 1 ? line.qty + ' × ' : '') + line.description));
            row.appendChild(el('span', 'amount', core.money(line.pence)));
            item.appendChild(row);
        });
        if (note.text) item.appendChild(el('p', 'words', note.text));
        var parts = [];
        if (note.followUp) parts.push('Recheck ' + core.shortDate(note.followUp, today()));
        if (note.attempts > 1 && note.status !== 'sent') parts.push(note.attempts + ' tries');
        if (parts.length) item.appendChild(el('small', '', parts.join(' · ')));
        note.files.forEach(function (file) { item.appendChild(fileNode(file)); });
        if (note.entry && note.status !== 'sent') {
            // A note the server has never been sent, or has refused, can
            // be opened again. One that was sent with no answer may be
            // in the practice's records already: it stays as it is.
            if (!note.entry.attempts || note.status === 'failed') {
                item.appendChild(button('link calm', 'Change', function () { changeEntry(note.entry); }));
            }
            item.appendChild(button('link', 'Remove', function () { removeEntry(note.entry); }));
        }
        return item;
    }

    // The addresses the browser made for photos and recordings that are
    // open on the screen. Given back when the screen changes.
    var mediaUrls = [];
    function freeMedia() {
        mediaUrls.forEach(function (url) { URL.revokeObjectURL(url); });
        mediaUrls = [];
    }

    // The file itself: from the phone while the note waits there, else
    // from the practice's records, which needs a signal.
    function loadFile(file) {
        if (file.local) return Promise.resolve(new Blob([file.local.buffer], { type: file.local.type }));
        return fetch(file.url, { credentials: 'same-origin', cache: 'no-store' }).then(function (response) {
            if (!response.ok || response.redirected) throw new Error('not available');
            return response.blob();
        });
    }

    // A photo or a recording of a note: one tap opens it in place.
    function fileNode(file) {
        var voice = file.kind === 'audio';
        var name = voice ? 'voice note' : 'photo';
        var box = el('div', 'file');
        if (!file.local && !file.url) {
            box.appendChild(el('small', '', 'With a ' + name + ', in the practice\'s records.'));
            return box;
        }
        var open = button('link calm', (voice ? '▶ Play ' : 'Show ') + name + ' (' + core.formatBytes(file.bytes) + ')', function () {
            open.disabled = true;
            open.textContent = 'Loading the ' + name + '…';
            loadFile(file).then(function (blob) {
                var url = URL.createObjectURL(blob);
                mediaUrls.push(url);
                var node = el(voice ? 'audio' : 'img');
                if (voice) { node.controls = true; node.autoplay = true; }
                else node.alt = 'Photo';
                node.src = url;
                box.textContent = '';
                box.appendChild(node);
            }).catch(function () {
                open.disabled = false;
                open.textContent = 'No signal for the ' + name + '. Tap to try again.';
            });
        });
        box.appendChild(open);
        return box;
    }

    // `quiet`: the load the app does by itself when a horse opens. It
    // says nothing when there is no signal.
    function loadOlderNotes(quiet) {
        var horse = state.draft && state.draft.horse;
        if (!horse) return;
        var message = $('history-message');
        if (!quiet) message.textContent = 'Loading…';
        var address = config.notesUrl + (horse.own ? '?client_horse=' : '?horse=') + encodeURIComponent(horse.id);
        request(address, {}, 20000).then(function (result) {
            if (!state.draft || state.draft.horse !== horse) return;
            if (result.kind === 'ok' && result.status === 200) {
                state.olderNotes = result.data.notes;
                message.textContent = '';
                renderHistory();
            } else if (quiet) {
                return;
            } else if (result.kind === 'offline') {
                message.textContent = 'No signal. Older notes need a signal.';
            } else {
                message.textContent = 'Sign in again to load older notes.';
            }
        });
    }

    // A send that is in progress writes to the phone's storage when it
    // ends. A removal in the middle of it would be undone.
    function busySending() {
        if (!state.syncing) return false;
        say('The phone is sending. Try again in a moment.');
        return true;
    }

    // Open a note that has not gone again, as it was: its items, its
    // words, its recheck date, its photo and its recording. It leaves the
    // list of notes and is the note in progress, which the phone keeps
    // as it is typed; Save puts it back.
    function changeEntry(entry) {
        if (busySending()) return;
        var horse = state.horsesById[entry.horseId];
        if (!horse) { say('This horse is not on the phone now. The note stays as it is.'); return; }
        if (state.view === 'horse' && !draftIsEmpty() && !window.confirm('Leave the note you are writing, to change this one?')) return;
        function file(stored, bytes) {
            return stored ? { buffer: stored.buffer, type: stored.type, bytes: bytes, sha256: stored.sha256 } : null;
        }
        remove('entries', entry.clientId).then(function () {
            state.entries.splice(state.entries.indexOf(entry), 1);
            openHorse(horse, {
                note: entry.note, followUp: entry.followUp,
                lines: (entry.lines || []).map(function (line) { return Object.assign({ custom: !line.priceId }, line); }),
                photo: file(entry.photo, entry.photoBytes), audio: file(entry.audio, entry.audioBytes)
            });
            persistDraft();
            say('The note is open again. Tap Save when it is right.', true);
        });
    }

    function removeEntry(entry) {
        if (busySending()) return;
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
            showAttachments();
            persistDraft();
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
            // The seconds, so the vet sees that it is recording.
            var clock = setInterval(function () {
                $('audio-state').textContent = 'Recording: ' + Math.round((Date.now() - started) / 1000) + ' s of 60.';
            }, 1000);
            recorder.ondataavailable = function (event) {
                if (event.data && event.data.size) chunks.push(event.data);
            };
            recorder.onstop = function () {
                clearTimeout(timer);
                clearInterval(clock);
                stream.getTracks().forEach(function (track) { track.stop(); });
                state.recorder = null;
                $('record').textContent = 'Record voice';
                $('record').classList.remove('recording');
                var blob = new Blob(chunks, { type: recorder.mimeType || type || 'audio/mp4' });
                if (!blob.size) {
                    $('audio-state').textContent = 'Nothing was recorded. Try again.';
                    return;
                }
                toStored(blob).then(function (stored) {
                    if (state.draft !== draft) return;
                    draft.audio = stored;
                    showAttachments();
                    persistDraft();
                });
            };
            state.recorder = recorder;
            recorder.start();
            $('record').textContent = 'Stop recording';
            $('record').classList.add('recording');
            $('audio-state').textContent = 'Recording: 0 s of 60.';
        }).catch(function (error) {
            $('audio-state').textContent = 'The microphone is not available: ' + error.message;
        });
    }

    function stopRecording() {
        if (state.recorder && state.recorder.state !== 'inactive') state.recorder.stop();
    }

    // One visit for each client for each day. Its id is made here, with
    // the first note of the day, and goes with every note after it. A
    // note after Finish visit starts a new one.
    function currentVisit(clientId, now) {
        var day = core.localDay(now);
        var mine = visits()[clientId];
        if (mine && mine.id && mine.day === day && !visitDone(mine)) return mine;
        // The same vet on a phone and on a computer: a visit that is
        // open for this client today in the practice's records is the
        // visit. A second one would be a second bill.
        var open = (state.meta.visitList || []).filter(function (visit) {
            return visit.client_id === clientId && visit.date === day && visit.status === 'open' &&
                !closed()[visit.id] && visit.author_id === state.meta.userId;
        })[0];
        return open ? { id: open.id, day: day, finished: false } : core.visitForDay(null, day, newId);
    }

    function newEntry(horse, visit, now, parts) {
        var photo = parts.photo || null;
        var audio = parts.audio || null;
        return {
            clientId: newId(),
            visitId: visit.id,
            // Whose visit it is. Not `clientId`: that is the id of the note.
            ownerId: clientIdOf(horse),
            ownerName: (clientById(clientIdOf(horse)) || {}).name || '',
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
        // A refusal says why, next to Save, and takes the vet to the box.
        function goTo(selector) {
            var box = $('lines').querySelector(selector);
            if (box) { box.scrollIntoView({ block: 'center' }); box.focus({ preventScroll: true }); }
        }
        if (draft.lines.some(function (line) { return line.badPrice; })) {
            message.textContent = 'A price is not a number. It is marked in red.';
            goTo('input.bad');
            return;
        }
        var unnamed = draft.lines.filter(function (line) { return !line.description.trim(); })[0];
        if (unnamed) {
            message.textContent = 'Type what the other item is.';
            goTo('li:nth-child(' + (draft.lines.indexOf(unnamed) + 1) + ') input');
            return;
        }
        if (!note && !draft.lines.length && !draft.photo && !draft.audio) {
            message.textContent = 'Tap an item, type a note, take a photo or record your voice first.';
            return;
        }
        var now = new Date();
        var clientId = clientIdOf(draft.horse);
        var visit = currentVisit(clientId, now);
        var entry = newEntry(draft.horse, visit, now, {
            note: note, followUp: draft.followUp, lines: draft.lines, photo: draft.photo, audio: draft.audio
        });
        $('save').disabled = true;
        askToKeepStorage();
        keepVisit(clientId, visit).then(function () {
            return put('entries', entry);
        }).then(function () {
            state.entries.push(entry);
            state.draft = null;
            setPreview(null);
            forgetDraft();
            say(draft.horse.name + ' saved.');
            show('client');
            refreshStorage();
            sync(false);
        }).catch(function (error) {
            // The note is still on the screen: nothing is lost.
            message.textContent = 'The phone could not save this note: ' + error.message;
        }).then(function () {
            $('save').disabled = false;
        });
    }

    // ── Visits ───────────────────────────────────────────────────────────
    //
    // Every visit the phone knows, each one folded to a line: the open
    // ones to finish, then the finished ones. A visit comes from the
    // notes on this phone, or from the practice's records (loaded with a
    // signal and kept for the next time there is none), or from both.

    // A part that folds. `body(box)` fills it the first time it opens.
    // `state.open` keeps which parts are open while the screen is drawn again.
    function fold(key, className, summary, body, openFirst) {
        var node = el('details', 'fold ' + className);
        var head = el('summary');
        summary.forEach(function (part) { head.appendChild(part); });
        node.appendChild(head);
        var built = false;
        function build() {
            if (built) return;
            built = true;
            var box = el('div', 'body');
            body(box);
            node.appendChild(box);
        }
        if (state.open[key] === undefined ? openFirst : state.open[key]) {
            node.open = true;
            build();
        }
        node.addEventListener('toggle', function () {
            state.open[key] = node.open;
            if (node.open) build();
        });
        return node;
    }

    // `only`: the visits of one client. Left out: every client's.
    function visitRows(only) {
        var byId = {};
        var rows = [];
        var done = closed();
        var queue = finishQueue();
        (state.meta.visitList || []).forEach(function (visit) {
            rows.push(byId[visit.id] = {
                id: visit.id, clientId: visit.client_id || yardClientId(), clientName: visit.yard || 'Client',
                day: visit.date, open: visit.status !== 'finished', horses: visit.horses,
                total: core.toPence(visit.total) || 0, bills: visit.bills || [], billWhen: visit.bill_when,
                finishedAt: visit.finished_at, entries: [], unsent: 0, onServer: true
            });
        });
        core.localVisits(state.entries).forEach(function (local) {
            var row = byId[local.id];
            if (!row) {
                var clientId = local.clientId && local.clientId !== OLD_YARD ? local.clientId : yardClientId();
                var client = clientById(clientId);
                rows.push(row = byId[local.id] = {
                    id: local.id, clientId: clientId, clientName: (client && client.name) || local.clientName || yardName(),
                    day: local.day, open: true, bills: [], entries: [], unsent: 0, onServer: false
                });
            }
            var notes = local.entries.map(noteView);
            row.entries = local.entries;
            row.unsent = notes.filter(function (note) { return note.status !== 'sent'; }).length;
            // The phone knows more than the list while a note waits on it.
            if (row.unsent || !row.onServer) {
                var groups = core.groupNotes(notes);
                row.horses = groups.length;
                row.total = groups.reduce(function (sum, group) { return sum + group.total; }, 0);
            }
        });
        rows.forEach(function (row) {
            var mine = done[row.id];
            if (mine) {
                row.open = false;
                row.finishedAt = row.finishedAt || mine.finishedAt;
                if (mine.bills && !row.bills.length) row.bills = mine.bills;
                row.billWhen = row.billWhen || mine.billWhen;
            }
            row.waiting = queue.some(function (item) { return item.id === row.id; });
        });
        return rows.filter(function (row) {
            return !only || row.clientId === only;
        }).sort(function (a, b) { return a.day < b.day ? 1 : a.day > b.day ? -1 : 0; });
    }

    function renderVisits() {
        var rows = visitRows();
        var open = rows.filter(function (row) { return row.open; });
        var past = rows.filter(function (row) { return !row.open; });
        fill($('open-visits'), open.map(function (row) { return visitFold(row); }));
        fill($('past-visits'), dayGroups(past, [], false));
        $('open-visits-box').hidden = open.length === 0;
        $('past-visits-box').hidden = past.length === 0;
        $('visits-empty').hidden = rows.length > 0;
    }

    // "Bill CV-0001" once the server has made it.
    function billLabel(row) {
        if (row.waiting || row.unsent) return 'not all sent yet';
        if (row.bills.length) {
            // Paid, when the phone's list of invoices says every one of them is.
            var numbers = row.bills.map(function (bill) { return bill.number; });
            var known = bills().filter(function (bill) { return numbers.indexOf(bill.number) !== -1; });
            var paid = known.length === numbers.length && known.every(function (bill) { return bill.paid_on; });
            return 'Bill ' + numbers.join(', ') + (paid ? ' · paid' : '');
        }
        return row.billWhen === 'month' ? 'on the monthly bill' : 'finished';
    }

    // One visit, folded: the client, the day, the horses and the total.
    // `underDay`: it is under the heading of its day, so its own line
    // does not say the day again. `ofClient`: it is on its client's own
    // timeline, so it does not say the client again.
    function visitFold(row, underDay, ofClient) {
        var day = today();
        var text = el('span', 'text', ofClient ? 'Visit' : row.clientName);
        text.appendChild(el('small', '', [
            underDay ? '' : core.shortDate(row.day, day), countText(row.horses, 'horse', 'horses'),
            row.open ? '' : billLabel(row)
        ].filter(Boolean).join(' · ')));
        var summary = [text];
        // Among finished visits, the one that still waits for Finish visit says so.
        if (row.open && underDay) summary.push(el('span', 'due soon', 'Open'));
        summary.push(el('span', 'amount', core.money(row.total)));
        var node = fold('v:' + row.id, 'card', summary, function (box) { visitBody(row, box); },
            row.open && row.day === day);
        node.dataset.visit = row.id;
        return node;
    }

    // The practice's record of one visit, for a visit this phone does
    // not hold, or holds without its photos and recordings.
    function loadVisit(row, box, quiet) {
        state.visitTried[row.id] = true;
        request(config.visitsUrl + row.id + '/', {}, 20000).then(function (result) {
            if (result.kind === 'ok' && result.status === 200) {
                state.visitDetails[row.id] = result.data;
                box.textContent = '';
                visitBody(row, box);
            } else if (!quiet) {
                box.textContent = '';
                box.appendChild(el('p', 'muted', result.kind === 'offline'
                    ? 'No signal. The notes of this visit are in the practice\'s records: they need a signal.'
                    : 'Sign in again to open this visit.'));
                box.appendChild(button('link calm', 'Try again', function () {
                    box.textContent = '';
                    visitBody(row, box);
                }));
            }
        });
    }

    function visitBody(row, box) {
        var detail = state.visitDetails[row.id];
        // The phone's own notes while one of them is not sent, or while
        // the record is not loaded. Else the record: it has every photo
        // and recording.
        var local = row.entries.length > 0 && (row.unsent > 0 || !detail);
        if (!local && !detail) {
            box.appendChild(el('p', 'muted', 'Loading…'));
            loadVisit(row, box, false);
            return;
        }
        if (local && !row.unsent && row.onServer && navigator.onLine && !state.visitTried[row.id]) {
            loadVisit(row, box, true);
        }
        var notes = local ? row.entries.map(noteView) : detail.note_list.map(core.noteFromServer);
        var groups = core.groupNotes(notes);
        var day = today();
        var toOwners = (detail ? detail.bill_to : billTo(row.clientId)) === 'owners';
        box.appendChild(el('p', 'muted', toOwners ? 'Bill to each owner' : 'Bill to ' + row.clientName));

        groups.forEach(function (group) {
            var horse = state.horsesById[group.horseKey];
            var text = el('span', 'text', group.horseName);
            var line = core.horseLine(group) + (group.followUp ? ' · recheck ' + core.shortDate(group.followUp, day) : '');
            text.appendChild(el('small', '', line));
            box.appendChild(fold('h:' + row.id + ':' + group.horseKey, 'inner',
                [text, el('span', 'amount', core.money(group.total))], function (inner) {
                    group.notes.forEach(function (note) { inner.appendChild(noteCard(note)); });
                    var owners = horse ? payersText(horse) : '';
                    if (owners) inner.appendChild(el('small', '', owners));
                    if (row.open && horse && horse.is_active !== false) {
                        inner.appendChild(button('link calm', 'Add more for ' + horse.name, function () { openHorse(horse); }));
                    }
                }, false));
        });

        var total = groups.reduce(function (sum, group) { return sum + group.total; }, 0);
        var vat = notes.reduce(function (sum, note) {
            return sum + note.lines.reduce(function (part, line) { return part + core.vatPart(line.pence, line.vatRate); }, 0);
        }, 0);
        if (toOwners && local) {
            // What each owner pays, as the phone's horse list has it.
            var summary = core.visitSummary(row.entries, state.horsesById);
            var payers = summary.payers.slice();
            if (summary.unallocated) payers.push({ name: 'No owner on record', pence: summary.unallocated });
            box.appendChild(el('h3', '', 'Who pays'));
            payers.forEach(function (payer) {
                var item = el('div', 'pay');
                item.appendChild(el('span', '', payer.name));
                item.appendChild(el('span', 'amount', core.money(payer.pence)));
                box.appendChild(item);
            });
        }
        box.appendChild(el('p', 'total', countText(groups.length, 'horse', 'horses') + ' · Total ' + core.money(total)));
        var client = clientById(row.clientId);
        box.appendChild(el('p', 'muted', 'Includes VAT of ' + core.money(vat) + '.' + (
            toOwners ? ' The practice confirms who pays when the notes arrive.'
                : client && client.is_yard ? ' ' + row.clientName + ' invoices each owner for their horse.' : '')));

        row.bills.forEach(function (bill) {
            if (!bill.url) return;
            var link = el('a', 'button wide', 'Bill ' + bill.number + ' · ' + core.money(pence(bill.total)) + ' (needs a signal)');
            link.href = bill.url;
            box.appendChild(link);
        });
        desk('visit', row, box);
        if (row.open) {
            var finish = button('button primary wide', 'Finish visit', function () { finishVisit(row); });
            finish.dataset.finish = row.id;
            box.appendChild(finish);
            return;
        }
        box.appendChild(el('p', 'muted', (row.finishedAt ? 'Visit finished ' + timeText(row.finishedAt) + '. ' : '') + (
            row.waiting || row.unsent ? 'Not all sent yet: keep the app open when you have a signal.'
                : 'All sent to the practice.' + (row.billWhen === 'month' && !row.bills.length ? ' It goes on the monthly bill.' : '')
        ) + ' A new note for this client starts a new visit.'));
    }

    function finishVisit(row) {
        var question = 'Finish this visit for ' + row.clientName + '? ' +
            countText(row.horses, 'horse', 'horses') + ', ' + core.money(row.total) + '.';
        if (!window.confirm(question)) return;
        var finishedAt = new Date().toISOString();
        // Found by the id of the visit, under whichever client it is kept.
        var key = Object.keys(visits()).filter(function (clientId) { return visits()[clientId].id === row.id; })[0];
        var done = Object.assign({}, closed());
        done[row.id] = { finishedAt: finishedAt };
        // The client's visit of today is closed: its next note starts a new one.
        var first = key
            ? keepVisit(key, Object.assign({}, visits()[key], { finished: true, finishedAt: finishedAt }))
            : Promise.resolve();
        first.then(function () {
            return setMeta('closed', done);
        }).then(function () {
            return setMeta('finishQueue', finishQueue().concat([{ id: row.id, finishedAt: finishedAt }]));
        }).then(function () {
            say('Visit finished.');
            render();
            sync(false);
        });
    }

    // The practice's list of visits, kept on the phone so that the
    // Visits screen has it with no signal. Not more than once in 30 s.
    var visitListAt = 0;
    function loadVisitList(now) {
        if (!now && (Date.now() - visitListAt < 30000 || !navigator.onLine)) {
            if (!navigator.onLine) $('visits-message').textContent = 'No signal. This is the list from the last time there was one.';
            return;
        }
        visitListAt = Date.now();
        request(config.visitsUrl, {}, 20000).then(function (result) {
            var message = result.kind === 'offline' ? 'No signal. This is the list from the last time there was one.' : '';
            if (result.kind === 'ok' && result.status === 200) {
                return setMeta('visitList', result.data.visits).then(function () {
                    $('visits-message').textContent = '';
                    if (state.view === 'visits') renderVisits();
                    else desk('view');
                });
            }
            $('visits-message').textContent = message;
            // The notes on the phone may have changed (sent, or not): show that.
            if (state.view === 'visits') renderVisits();
        }).catch(function () { /* the list on the phone stays */ });
    }

    // ── Invoices ─────────────────────────────────────────────────────────
    //
    // The practice's bills, read from its records with a signal and kept
    // on the phone, so the list is there with no signal too. A bill is
    // made by the server when a visit is finished; this screen shows it
    // and marks it paid.

    function bills() { return state.meta.billList || []; }
    function pence(text) { return core.toPence(text) || 0; }
    function sumOf(list) { return list.reduce(function (sum, bill) { return sum + pence(bill.total); }, 0); }

    var billListAt = 0;
    function loadBills(now) {
        if (!now && (Date.now() - billListAt < 30000 || !navigator.onLine)) {
            if (!navigator.onLine) $('bills-message').textContent = 'No signal. This is the list from the last time there was one.';
            return;
        }
        billListAt = Date.now();
        request(config.billsApiUrl, {}, 20000).then(function (result) {
            if (result.kind !== 'ok' || result.status !== 200) {
                if (result.kind === 'offline') $('bills-message').textContent = 'No signal. This is the list from the last time there was one.';
                return;
            }
            return setMeta('billList', result.data.bills).then(function () {
                return setMeta('unbilled', result.data.unbilled);
            }).then(function () {
                $('bills-message').textContent = '';
                if (state.view === 'bills') renderBills();
                else if (state.view === 'client' && state.clientTab !== 'horses') renderClient();
            });
        }).catch(function () { /* the list on the phone stays */ });
    }

    // A figure with its words, as C-gate's count pills.
    function stat(figure, words, hot) {
        var node = el('span', 'count' + (hot ? ' hot' : ''));
        node.appendChild(el('b', '', figure));
        node.appendChild(document.createTextNode(' ' + words));
        return node;
    }

    function paidPill(bill) {
        if (bill.paid_on) return el('span', 'pill done', 'Paid ' + core.shortDate(bill.paid_on, today()));
        // How long it has waited. Past 30 days it is red.
        var age = core.daysBetween(bill.date, today());
        return el('span', 'due ' + (age > 30 ? 'overdue' : 'soon'),
            'Not paid' + (age > 0 ? ' · ' + countText(age, 'day', 'days') : ''));
    }

    // The bills as a list: number and day, who it is to, the amount, paid or not.
    function billList(list, withClient) {
        var box = el('ul', 'list card');
        list.forEach(function (bill) {
            var item = el('li');
            var row = button('horse go', undefined, function () { openBill(bill); });
            var text = el('span', 'text');
            text.appendChild(el('span', 'strong', bill.number));
            var horses = (bill.horses || []).length;
            text.appendChild(el('small', '', [
                core.shortDate(bill.date, today()), withClient ? bill.to : '', countText(horses, 'horse', 'horses'),
                bill.emailed_at ? 'sent' : ''
            ].filter(Boolean).join(' · ')));
            row.appendChild(text);
            row.appendChild(paidPill(bill));
            row.appendChild(el('span', 'amount', core.money(pence(bill.total))));
            item.appendChild(row);
            box.appendChild(item);
        });
        return box;
    }

    function renderBills() {
        var all = bills();
        var unpaid = all.filter(function (bill) { return !bill.paid_on; });
        var paid = all.filter(function (bill) { return bill.paid_on; });
        var waiting = pence((state.meta.unbilled || {}).total);
        var counts = all.length ? [
            stat(core.money(sumOf(unpaid)), 'to pay', unpaid.length > 0),
            stat(core.money(sumOf(paid)), 'paid')
        ] : [];
        if (waiting) counts.push(stat(core.money(waiting), 'not on an invoice yet'));
        fill($('bill-counts'), counts);
        var filter = state.billFilter || '';
        fill($('bill-chips'), all.length ? [['', 'All'], ['unpaid', 'Not paid'], ['paid', 'Paid']].map(function (choice) {
            var chip = button('tile' + (filter === choice[0] ? ' on' : ''), choice[1], function () {
                state.billFilter = choice[0];
                renderBills();
            });
            chip.setAttribute('aria-pressed', filter === choice[0] ? 'true' : 'false');
            return chip;
        }) : []);
        var shown = filter === 'unpaid' ? unpaid : filter === 'paid' ? paid : all;
        fill($('bills'), shown.length ? [billList(shown, true)] : [el('p', 'empty', all.length
            ? 'No invoice fits this choice.'
            : 'No invoices yet. An invoice is made when a visit is finished and all of its notes are sent.')]);
    }

    // One invoice: each horse's items, the total, and paid or not.
    function openBill(bill) {
        var day = today();
        $('bill-title').textContent = 'Invoice ' + bill.number;
        var nodes = [el('p', 'muted', 'To ' + bill.to + ' · ' + core.shortDate(bill.date, '0') + (bill.period_start
            ? ' · visits from ' + core.shortDate(bill.period_start, day) + ' to ' + core.shortDate(bill.period_end, day) : ''))];
        (bill.horses || []).forEach(function (horse) {
            // Each horse, with what it comes to: the yard's own invoices go by horse.
            var head = el('div', 'head');
            head.appendChild(el('h3', '', horse.name));
            // With one item, the item's own amount says it.
            if (horse.lines.length > 1) {
                head.appendChild(el('span', 'amount', core.money(horse.lines.reduce(function (sum, line) {
                    return sum + pence(line.amount);
                }, 0))));
            }
            nodes.push(head);
            horse.lines.forEach(function (line) {
                var row = el('div', 'item');
                row.appendChild(el('span', '', (bill.period_start ? core.shortDate(line.date, day) + ' · ' : '') +
                    (Number(line.quantity) !== 1 ? Number(line.quantity) + ' × ' : '') + line.description));
                row.appendChild(el('span', 'amount', core.money(pence(line.amount))));
                nodes.push(row);
            });
        });
        nodes.push(el('p', 'total', 'Total ' + core.money(pence(bill.total))));
        nodes.push(el('p', 'muted', 'Includes VAT of ' + core.money(pence(bill.vat)) + '.' +
            (bill.emailed_at ? ' Sent ' + timeText(bill.emailed_at) + '.' : '')));
        var state_ = el('p', '');
        state_.appendChild(paidPill(bill));
        nodes.push(state_);
        var message = el('p', 'problem');
        var actions = el('div', 'row');
        actions.appendChild(button('button' + (bill.paid_on ? '' : ' primary'), bill.paid_on ? 'Mark as not paid' : 'Mark as paid today', function () {
            markPaid(bill, !bill.paid_on, message);
        }));
        var page = el('a', 'button', 'Print or send');
        page.href = bill.url;
        actions.appendChild(page);
        nodes.push(actions, message, el('p', 'muted', 'Both need a signal.'));
        fill($('bill-body'), nodes);
        $('bill-sheet').hidden = false;
    }

    // The mark is made on the server, so its code comes with the first
    // use, with the rest of what needs a signal (online.js).
    function markPaid(bill, paid, message) {
        online().then(function (part) { part.markPaid(bill, paid, message); }, function () {
            message.textContent = 'No signal. The invoice was not changed.';
        });
    }

    // ── One client: its timeline, and its invoices ───────────────────────

    // What is due for its horses, then its visits under the day of each.
    function clientTimeline(client, horses) {
        var day = today();
        var nodes = [];
        var due = [];
        horses.forEach(function (horse) {
            (horse.due || []).forEach(function (item) { due.push({ horse: horse, item: item }); });
        });
        due.sort(function (a, b) { return a.item.date < b.item.date ? -1 : a.item.date > b.item.date ? 1 : 0; });
        if (due.length) {
            nodes.push(el('h3', '', 'Due'));
            var list = el('ul', 'list card');
            due.forEach(function (entry) {
                var status = core.dueStatus(entry.item.date, day);
                var item = el('li');
                var row = button('horse go', undefined, function () { openHorse(entry.horse); });
                var text = el('span', 'text');
                text.appendChild(el('span', 'strong', entry.horse.name));
                text.appendChild(el('small', '', entry.item.label));
                row.appendChild(text);
                row.appendChild(el('span', status === 'later' ? 'pill' : 'due ' + status,
                    (status === 'overdue' ? 'Overdue ' : '') + core.shortDate(entry.item.date, day)));
                item.appendChild(row);
                list.appendChild(item);
            });
            nodes.push(list);
        }
        var rows = visitRows(client.id);
        if (rows.length) nodes.push(el('h3', '', 'Visits'));
        dayGroups(rows, nodes, true);
        if (!nodes.length) nodes.push(el('p', 'empty', 'Nothing yet. Tap a horse to start a visit.'));
        return nodes;
    }

    // Visits under the heading of their day: "Today", "Mon 16 Nov".
    function dayGroups(rows, nodes, ofClient) {
        core.byDay(rows).forEach(function (group) {
            nodes.push(el('p', 'day', core.dayLabel(group.day, today())));
            group.rows.forEach(function (row) { nodes.push(visitFold(row, true, ofClient)); });
        });
        return nodes;
    }

    function clientBills(client) {
        var mine = bills().filter(function (bill) { return bill.client_id === client.id; });
        var unpaid = mine.filter(function (bill) { return !bill.paid_on; });
        if (!mine.length) {
            return [el('p', 'empty', 'No invoices for this client yet. An invoice is made when a visit is finished.')];
        }
        var counts = el('div', 'counts');
        counts.appendChild(stat(core.money(sumOf(unpaid)), 'to pay', unpaid.length > 0));
        counts.appendChild(stat(core.money(sumOf(mine) - sumOf(unpaid)), 'paid'));
        return [counts, billList(mine, false)];
    }

    // ── Prices ───────────────────────────────────────────────────────────

    function renderPrices() {
        // Under their categories, as in the item list. Hidden prices too.
        var groups = {};
        var names = [];
        state.prices.forEach(function (price) {
            var name = price.category || 'Other';
            if (!groups[name]) { groups[name] = []; names.push(name); }
            groups[name].push(price);
        });
        names.sort(function (a, b) {
            if ((a === 'Other') !== (b === 'Other')) return a === 'Other' ? 1 : -1;
            return a.localeCompare(b);
        });
        var nodes = [];
        names.forEach(function (name) {
            if (names.length > 1) nodes.push(el('h3', '', name));
            var list = el('ul', 'list card');
            groups[name].forEach(function (price) {
                var item = el('li');
                var row = button('price go' + (price.isActive ? '' : ' hidden-price'), undefined, function () { editPrice(price); });
                var text = el('span', 'text');
                text.appendChild(el('span', 'strong', price.name));
                text.appendChild(el('small', '', (price.isActive ? '' : 'Hidden · ') + 'VAT ' + Number(price.vatRate) + '%' +
                    (price.vaccinationTypeId ? ' · records a vaccination' : '')));
                row.appendChild(text);
                row.appendChild(el('span', 'amount', core.money(price.pence)));
                item.appendChild(row);
                list.appendChild(item);
            });
            nodes.push(list);
        });
        fill($('prices'), nodes);
        var editing = state.editingPrice;
        $('price-form').hidden = !state.priceFormOpen;
        $('price-add').hidden = !!state.priceFormOpen;
        $('price-form-title').textContent = editing ? 'Change a price' : 'Add a price';
        $('price-hide').hidden = !editing;
        $('price-hide').textContent = editing && !editing.isActive ? 'Show' : 'Hide';
        desk('prices');
    }

    // To change the price list and to sign out need a signal, so their
    // code is in a file of its own (online.js) that the phone fetches the
    // first time it is needed, and keeps. It is not part of the download
    // that opens the app.
    var onlineLoading = null;
    function online() {
        if (window.CvetOnline) return Promise.resolve(window.CvetOnline);
        if (!onlineLoading) {
            onlineLoading = new Promise(function (resolve, reject) {
                var script = el('script');
                script.src = config.onlineJs;
                script.onload = function () { resolve(window.CvetOnline); };
                script.onerror = function () { onlineLoading = null; script.remove(); reject(new Error('no signal')); };
                document.head.appendChild(script);
            });
        }
        return onlineLoading;
    }

    // Open the form for a new price (null), or for one of the list.
    function editPrice(price) {
        online().then(function (part) { part.editPrice(price); }, function () {
            say('No signal. A price change needs a signal.');
        });
    }

    function signOut() {
        if (busySending()) return;
        online().then(function (part) { part.signOut(); }, function () {
            say('No signal. Sign out needs a signal. To remove the data now, tap Clear this phone.');
        });
    }

    function fromServerPrice(item) {
        return {
            id: item.id, name: item.name, category: item.category || '',
            pence: core.toPence(item.price) || 0, vatRate: item.vat_rate,
            isActive: item.is_active, vaccinationTypeId: item.vaccination_type_id || null
        };
    }

    // ── A client and a horse: add, change, archive ───────────────────────
    //
    // Saved on the phone at once, so a new client or a new horse can be
    // made at a yard with no signal, and sent with the next sync
    // (`sendChanges`). The C-gate yard and its horses are not changed here.

    function editClient(client) {
        state.editingClient = client;
        var kinds = $('client-form-kind');
        if (!kinds.options.length) {
            fill(kinds, core.KINDS.map(function (kind) {
                var option = el('option', '', kind[1]);
                option.value = kind[0];
                return option;
            }));
        }
        $('client-form-title').textContent = client ? 'Change client' : 'Add client';
        $('client-form-name').value = client ? client.name : '';
        kinds.value = client ? client.kind : 'yard';
        $('client-form-contact').value = client ? client.contact_name || '' : '';
        $('client-form-phone').value = client ? client.phone || '' : '';
        $('client-form-email').value = client ? client.email || '' : '';
        $('client-form-address').value = client ? client.address || '' : '';
        $('client-form-archive').hidden = !client;
        $('client-form-archive').textContent = client && client.is_active === false ? 'Restore' : 'Archive';
        $('client-form-message').textContent = '';
        $('client-sheet').hidden = false;
        $('client-form-name').focus();
    }

    function saveClient(event, flipActive) {
        if (event) event.preventDefault();
        var editing = state.editingClient;
        var name = $('client-form-name').value.trim();
        if (!name) { $('client-form-message').textContent = 'Type the name of the client.'; return; }
        var client = Object.assign({}, editing || { id: newId(), is_yard: false, bill_to: 'yard', is_active: true }, {
            name: name, kind: $('client-form-kind').value,
            contact_name: $('client-form-contact').value.trim(), phone: $('client-form-phone').value.trim(),
            email: $('client-form-email').value.trim(), address: $('client-form-address').value.trim(),
            unsent: true
        });
        delete client.refused;
        if (flipActive) client.is_active = client.is_active === false;
        put('clients', client).then(function () {
            setClients(state.clients.filter(function (other) { return other.id !== client.id; }).concat([client]));
            $('client-sheet').hidden = true;
            say(client.name + (flipActive ? (client.is_active ? ' restored.' : ' archived.') : ' saved.'));
            if (!editing) openClient(client.id);
            else if (client.is_active === false) show('clients');
            else render();
            sync(false);
        }).catch(function (error) {
            $('client-form-message').textContent = 'The phone could not save this client: ' + error.message;
        });
    }

    function editHorse(horse) {
        state.editingHorse = horse;
        $('horse-form-title').textContent = horse ? 'Change horse' : 'Add horse';
        $('horse-form-name').value = horse ? horse.name : '';
        $('horse-form-owner').value = horse ? horse.owner || '' : '';
        $('horse-form-microchip').value = horse ? horse.microchip || '' : '';
        $('horse-form-passport').value = horse ? horse.passport || '' : '';
        $('horse-form-about').value = horse ? horse.about || '' : '';
        $('horse-form-archive').hidden = !horse;
        $('horse-form-archive').textContent = horse && horse.is_active === false ? 'Restore' : 'Archive';
        $('horse-form-message').textContent = '';
        $('horse-sheet').hidden = false;
        $('horse-form-name').focus();
    }

    function saveHorse(event, flipActive) {
        if (event) event.preventDefault();
        var editing = state.editingHorse;
        var name = $('horse-form-name').value.trim();
        if (!name) { $('horse-form-message').textContent = 'Type the name of the horse.'; return; }
        var horse = Object.assign({}, editing || {
            id: newId(), client_id: state.client, location: '', payers: [], due: [], own: true, is_active: true
        }, {
            name: name, owner: $('horse-form-owner').value.trim(),
            microchip: $('horse-form-microchip').value.trim(), passport: $('horse-form-passport').value.trim(),
            about: $('horse-form-about').value.trim(),
            unsent: true
        });
        delete horse.refused;
        if (flipActive) horse.is_active = horse.is_active === false;
        put('horses', horse).then(function () {
            setHorses(state.horses.filter(function (other) { return other.id !== horse.id; }).concat([horse]));
            if (state.draft && state.draft.horse.id === horse.id) state.draft.horse = horse;
            $('horse-sheet').hidden = true;
            say(horse.name + (flipActive ? (horse.is_active ? ' restored.' : ' archived.') : ' saved.'));
            // An archived horse has no treatment screen: back to its client.
            if (horse.is_active === false && state.view === 'horse') {
                state.draft = null;
                forgetDraft();
                show('client');
            } else {
                render();
            }
            sync(false);
        }).catch(function (error) {
            $('horse-form-message').textContent = 'The phone could not save this horse: ' + error.message;
        });
    }

    // Send each client and each horse that was made or changed on the
    // phone: clients first, so that a new horse finds its client.
    // Resolves to '' or to why it stopped.
    function sendChanges(csrf) {
        var queue = pendingChanges().filter(function (item) { return item.id !== OLD_YARD; }).map(function (item) {
            return { item: item, isHorse: state.horsesById[item.id] === item };
        });
        function next() {
            var job = queue.shift();
            if (!job) return Promise.resolve('');
            var item = job.item;
            var isHorse = job.isHorse;
            var form = new FormData();
            form.append('id', item.id);
            form.append('name', item.name);
            form.append('is_active', item.is_active === false ? 'false' : 'true');
            if (isHorse) {
                form.append('client_id', item.client_id);
                form.append('owner_name', item.owner || '');
                form.append('microchip', item.microchip || '');
                form.append('passport', item.passport || '');
                form.append('about', item.about || '');
            } else {
                ['kind', 'contact_name', 'phone', 'email', 'address'].forEach(function (field) {
                    form.append(field, item[field] || '');
                });
            }
            return post(isHorse ? config.horsesUrl : config.clientsUrl, form, csrf).then(function (result) {
                var what = result.kind === 'ok' ? core.outcome(result.status)
                    : result.kind === 'signin' ? 'signin' : 'later';
                // "Not found": C-vet is turned off on the server. Try later.
                if (result.status === 404) what = 'later';
                if (what !== 'sent' && what !== 'failed') return what;
                // Saved again while this was on its way: the newer one
                // stays in the queue and goes with the next sync.
                if ((isHorse ? state.horsesById : state.clientsById)[item.id] !== item) return next();
                delete item.unsent;
                // The server will never take it as it is: say so on its row.
                if (what === 'failed') item.refused = (result.data && result.data.error) || 'refused';
                return put(isHorse ? 'horses' : 'clients', item).then(next);
            });
        }
        return next();
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
        var name = state.meta.userName || 'Not signed in on this phone yet';
        $('who').textContent = name;
        $('who-initial').textContent = name.charAt(0).toUpperCase();
        $('who-practice').textContent = state.meta.practice || '';
        var waiting = unsentEntries().length + pendingChanges().length + finishQueue().length;
        $('sync-line').textContent = (waiting ? waiting + ' to send. ' : 'All sent. ') +
            (state.meta.lastSyncAt ? 'Last sent ' + timeText(state.meta.lastSyncAt) + '.' : '');
        $('downloaded').textContent = state.meta.downloadedAt
            ? countText(state.clients.length, 'client', 'clients') + ' and ' +
              countText(state.horses.length, 'horse', 'horses') + ', downloaded ' + timeText(state.meta.downloadedAt) + '.'
            : 'Clients, horses and prices. Needs a signal.';
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

    // ── Download: clients, horses and prices ─────────────────────────────

    function setHorses(horses) {
        state.horses = horses;
        state.horsesById = {};
        horses.forEach(function (horse) { state.horsesById[horse.id] = horse; });
    }

    // `quiet`: the refresh the app does by itself when it opens. It says
    // nothing unless it brought new data.
    function download(quiet) {
        // After the send that is in progress: a client that the send has
        // just delivered must be in the list this download brings.
        if (state.syncing && state.syncPromise) {
            return state.syncPromise.then(function () { return download(quiet); });
        }
        state.downloading = true;
        var buttons = [$('download'), $('download-first')];
        buttons.forEach(function (node) { node.disabled = true; });
        if (!quiet) {
            say('Downloading clients and horses…');
            askToKeepStorage();
        }
        return request(config.yardUrl, {}, 30000).then(function (result) {
            if (result.kind === 'offline') {
                if (!quiet) say('No signal. The clients and horses on this phone have not changed.');
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
                    say('Sign in again to download clients and horses.', true);
                    showSignIn(true);
                }
                return;
            }
            var data = result.data;
            var otherPerson = !!state.meta.userId && state.meta.userId !== data.user.id;
            if (otherPerson && (unsentEntries().length || pendingChanges().length || finishQueue().length)) {
                if (!quiet) say(otherPersonText());
                return;
            }
            showSignIn(false);
            var prices = (data.prices || []).map(fromServerPrice);
            // A client or a horse that was made or changed on this phone
            // and is not sent yet stays as the phone has it.
            function merged(fromServer, onPhone) {
                var waiting = onPhone.filter(function (item) { return item.unsent || item.refused; });
                var ids = {};
                waiting.forEach(function (item) { ids[item.id] = true; });
                return fromServer.filter(function (item) { return !ids[item.id]; }).concat(waiting);
            }
            var clients = merged(data.clients || [], otherPerson ? [] : state.clients);
            var horses = merged(data.horses || [], otherPerson ? [] : state.horses);
            var wasOldYard = !!state.clientsById[OLD_YARD];
            // Nothing of the person before stays: notes, visits, a note
            // in progress. All of it was sent (checked above).
            var fresh = !otherPerson ? Promise.resolve() : replaceAll('entries', []).then(function () {
                state.entries = [];
                state.visitDetails = {};
                return Promise.all(['visits', 'closed', 'finishQueue', 'visitList', 'billList', 'unbilled', 'draft'].map(function (key) {
                    return setMeta(key, null);
                }));
            });
            return fresh.then(function () {
                return replaceAll('horses', horses);
            }).then(function () {
                return replaceAll('clients', clients);
            }).then(function () {
                return replaceAll('prices', prices);
            }).then(function () {
                setHorses(horses);
                setClients(clients);
                state.prices = prices;
                if (state.draft) state.draft.horse = state.horsesById[state.draft.horse.id] || state.draft.horse;
                if (!wasOldYard) return;
                // The yard has its id as a client now: its open visit
                // and the screen follow it.
                var all = Object.assign({}, visits());
                if (state.client === OLD_YARD) state.client = yardClientId();
                if (!all[OLD_YARD]) return;
                all[yardClientId()] = all[OLD_YARD];
                delete all[OLD_YARD];
                return setMeta('visits', all);
            }).then(function () {
                return setMeta('downloadedAt', data.generated_at);
            }).then(function () {
                return setMeta('userId', data.user.id);
            }).then(function () {
                return setMeta('userName', data.user.name);
            }).then(function () {
                var yard = data.yard;
                return setMeta('yard', yard ? { name: yard.name || '', billTo: yard.bill_to || 'yard' } : null);
            }).then(function () {
                fill($('price-vaccination'), []);
                return setMeta('vaccinationTypes', data.vaccination_types || []);
            }).then(function () {
                return rememberSession(data);
            }).then(function () {
                if (!quiet) say('Clients and horses downloaded.');
                // And the practice's visits: a visit that is open for a
                // client today is joined, not made again (currentVisit).
                loadVisitList(true);
            });
        }).catch(function (error) {
            say('The phone could not store the download: ' + error.message, true);
        }).then(function () {
            buttons.forEach(function (node) { node.disabled = false; });
            state.downloading = false;
            // A note in progress keeps its boxes and the keyboard.
            if (state.view === 'horse') renderBar();
            else render();
            refreshStorage();
            // What was saved while the download ran goes now.
            sync(false);
        });
    }

    // ── Sync ─────────────────────────────────────────────────────────────

    function sendEntry(entry, csrf) {
        var form = new FormData();
        form.append('client_id', entry.clientId);
        if (entry.visitId) form.append('visit_id', entry.visitId);
        // A number: a horse of the C-gate yard. Else a horse of the
        // practice's own client, by the id the phone or the server made.
        form.append(/^\d+$/.test(String(entry.horseId)) ? 'horse_id' : 'client_horse_id', String(entry.horseId));
        form.append('horse_name', entry.horseName);
        if (entry.ownerId && entry.ownerId !== OLD_YARD) {
            form.append('visit_client_id', entry.ownerId);
            form.append('client_name', entry.ownerName || '');
        }
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
                var done = Object.assign({}, closed());
                var keep = Promise.resolve();
                if (what === 'sent') {
                    done[item.id] = {
                        finishedAt: item.finishedAt, bills: result.data.bills || [],
                        billWhen: result.data.bill_when || 'visit'
                    };
                    // The record has the bill now: read it again when asked.
                    delete state.visitDetails[item.id];
                    delete state.visitTried[item.id];
                    keep = setMeta('closed', done);
                }
                return keep.then(function () {
                    return setMeta('finishQueue', finishQueue().filter(function (left) {
                        return left.id !== item.id;
                    }));
                }).then(next);
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
        if (state.syncing) return state.syncPromise || Promise.resolve();
        // A download sends what waits when it ends.
        if (state.downloading) return Promise.resolve();
        var queue = unsentEntries().filter(function (entry) {
            return byHand || entry.status !== 'failed';
        });
        if (!queue.length && !finishQueue().length && !pendingChanges().length) {
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
                    // The server holds the files now. Free the space, and
                    // keep each id: the file is read back by it.
                    if (entry.photo) entry.photoId = entry.photo.id;
                    if (entry.audio) entry.audioId = entry.audio.id;
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

        var changesBefore = pendingChanges().length;
        state.syncPromise = getSession().then(function (result) {
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
                // Clients and horses first: a note may be for a new one.
                return sendChanges(session.csrf);
            }).then(function (why) {
                stop = why;
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
                say((sent === 1 ? '1 note sent.' : sent ? sent + ' notes sent.' : 'Everything is sent.') +
                    (left ? ' ' + left + ' could not be sent: open the horse to see why.' : ''));
            }
            if (sent) setMeta('lastSyncAt', new Date().toISOString());
            // A visit screen that is open keeps its place: only the bar
            // and the list of visits are brought up to date.
            var changed = sent > 0 || byHand || pendingChanges().length !== changesBefore;
            if (!changed) {
                // Nothing went (no signal): the screen stays as it is. A
                // redraw would close the keyboard and stop a recording
                // that is playing.
                renderBar();
            } else if (state.view === 'visits') {
                renderBar();
                loadVisitList(true);
                loadBills(true);
            } else if (state.view === 'horse') {
                // The note in progress keeps its boxes. Its history shows what was sent.
                renderBar();
                if (sent && state.draft) renderHistory();
            } else {
                render();
            }
            refreshStorage();
        });
        return state.syncPromise;
    }

    // ── Sign out and clear ───────────────────────────────────────────────

    function lossQuestion(action, plain) {
        // Notes, new or changed clients and horses, and finished visits
        // that the practice has not received.
        var waiting = [];
        var count = unsentEntries().length;
        var changes = pendingChanges().length;
        var finishes = finishQueue().length;
        if (count) waiting.push(countText(count, 'note', 'notes'));
        if (changes) waiting.push(countText(changes, 'client or horse', 'clients and horses'));
        if (finishes) waiting.push(countText(finishes, 'finished visit', 'finished visits'));
        return waiting.length
            ? 'Not sent yet: ' + waiting.join(', ') + '. ' + action + ' deletes this from the phone for good. Sync first if you can. Go on?'
            : plain;
    }

    function clearPhone() {
        if (busySending()) return;
        if (!window.confirm(lossQuestion('Clearing', 'Remove the clients, the horses and all notes from this phone?'))) return;
        clearEverything().then(function () {
            setHorses([]);
            setClients([]);
            state.prices = [];
            state.entries = [];
            state.meta = {};
            state.draft = null;
            state.visitDetails = {};
            say('This phone is clear. You are still signed in.');
            show('clients');
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
        $('client-search').addEventListener('input', renderClients);
        $('client-add').addEventListener('click', function () { editClient(null); });
        $('client-back').addEventListener('click', function () { show('clients'); });
        $('client-edit').addEventListener('click', function () { editClient(clientById(state.client)); });
        $('client-visit').addEventListener('click', function () {
            var visit = visits()[state.client];
            if (visit) state.open['v:' + visit.id] = true;
            show('visits');
        });
        $('client-form').addEventListener('submit', function (event) { saveClient(event, false); });
        $('client-form-archive').addEventListener('click', function () { saveClient(null, true); });
        $('client-form-cancel').addEventListener('click', function () { $('client-sheet').hidden = true; });
        $('horse-add').addEventListener('click', function () { editHorse(null); });
        $('horse-edit').addEventListener('click', function () { if (state.draft) editHorse(state.draft.horse); });
        $('horse-form').addEventListener('submit', function (event) { saveHorse(event, false); });
        $('horse-form-archive').addEventListener('click', function () { saveHorse(null, true); });
        $('horse-form-cancel').addEventListener('click', function () { $('horse-sheet').hidden = true; });
        $('search').addEventListener('input', renderClient);
        $('select-toggle').addEventListener('click', toggleSelecting);
        $('select-add').addEventListener('click', function () { openPicker('selected'); });
        $('item-add').addEventListener('click', function () { openPicker('draft'); });
        $('picker-search').addEventListener('input', renderPicker);
        $('picker-cancel').addEventListener('click', closePicker);
        $('picker-other').addEventListener('click', function () {
            closePicker();
            addLine({ id: newId(), priceId: '', description: '', qty: 1, unitPence: 0, vatRate: '20.00', custom: true });
            var box = $('lines').querySelector('li:last-child input');
            if (box) box.focus();
        });
        $('back').addEventListener('click', function () { if (leaveHorse()) show('client'); });
        $('follow-up').addEventListener('change', function () {
            if (!state.draft) return;
            state.draft.followUp = $('follow-up').value;
            renderFollowUps();
            persistDraft();
        });
        $('note').addEventListener('input', function () { growNote(); renderSuggestion(); persistDraft(); });
        $('photo').addEventListener('change', onPhotoChosen);
        $('record').addEventListener('click', toggleRecording);
        $('save').addEventListener('click', saveDraft);
        $('load-history').addEventListener('click', function () { loadOlderNotes(false); });
        $('horse-last').addEventListener('click', function () { $('history').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
        $('price-add').addEventListener('click', function () { editPrice(null); });
        // A tap on the dark area round a sheet closes it. So does Escape.
        $('bill-close').addEventListener('click', function () { $('bill-sheet').hidden = true; });
        ['picker', 'client-sheet', 'horse-sheet', 'bill-sheet'].forEach(function (id) {
            $(id).addEventListener('click', function (event) {
                if (event.target !== $(id)) return;
                if (id === 'picker') closePicker();
                else $(id).hidden = true;
            });
        });
        document.addEventListener('keydown', function (event) {
            if (event.key !== 'Escape') return;
            closePicker();
            $('client-sheet').hidden = true;
            $('horse-sheet').hidden = true;
            $('bill-sheet').hidden = true;
        });
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

        // The window is made wide or narrow: the same screen, laid out again.
        if (wideScreen && wideScreen.addEventListener) {
            wideScreen.addEventListener('change', function () {
                loadDesk();
                show(state.view === 'client' && !isWide() ? 'client' : state.view);
            });
        }
        // What desk.js works with.
        window.CvetApp = {
            state: state, core: core, config: config, $: $, el: el, button: button, fill: fill, say: say,
            isWide: isWide, visitRows: visitRows, visitFold: visitFold, editPrice: editPrice,
            renderPrices: renderPrices, loadVisitList: loadVisitList, clientById: clientById,
            // For online.js.
            setMeta: setMeta, bills: bills, openBill: openBill, render: render,
            newId: newId, getSession: getSession, request: request, post: post, put: put,
            fromServerPrice: fromServerPrice, lossQuestion: lossQuestion, clearEverything: clearEverything,
            setHorses: setHorses, setClients: setClients, DENIED_TEXT: DENIED_TEXT
        };
        loadDesk();

        registerWorker();

        Promise.all(STORES.map(getAll)).then(function (stored) {
            stored[0].forEach(function (row) { state.meta[row.key] = row.value; });
            setHorses(stored[1]);
            state.prices = stored[2].sort(function (a, b) { return a.name.localeCompare(b.name); });
            state.entries = stored[3];
            setClients(stored[4]);
            // A phone from before C-vet had a client list held one visit:
            // the visit at the C-gate yard.
            var old = state.meta.visit;
            if (old && !state.meta.visits) {
                var all = {};
                all[yardClientId()] = old;
                state.meta.visits = all;
                if (old.finished) {
                    state.meta.closed = {};
                    state.meta.closed[old.id] = { finishedAt: old.finishedAt, bills: old.bills, billWhen: old.billWhen };
                }
            }
            var saved = state.meta.draft;
            var horse = saved && state.horsesById[saved.horseId];
            if (horse) {
                openHorse(horse, saved);
                say('Your note for ' + horse.name + ' is back. It was not saved yet.', true);
            } else {
                show('clients');
            }
            // The speed measure: time since the page started to load.
            // The plan's limit is 1 second (docs/C_VET_PLAN.md, section 5).
            if (window.performance && performance.now) state.openMs = Math.round(performance.now());
            refreshStorage();
            sync(false);
            if (navigator.onLine) { loadVisitList(true); loadBills(true); }
            // Fresh due dates and prices for today's visit, if the list
            // is from another day. Behind the screen: nothing waits for it.
            var age = state.meta.downloadedAt ? Date.now() - new Date(state.meta.downloadedAt).getTime() : 0;
            // Or if this phone has horses and no client list yet.
            var noList = state.meta.downloadedAt && !!state.clientsById[OLD_YARD];
            if ((age > REFRESH_AFTER_MS || noList) && navigator.onLine) download(true);
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
