/* C-vet offline prototype: the page (docs/C_VET_PLAN.md, phase 1).

   Throwaway code. It answers one question on a real phone: can a web app
   hold a horse list, notes, photos and voice recordings with no signal,
   and send each note exactly once when the signal comes back?

   Where things are kept:
     - the page and its files: the service worker's store (templates/cvet/sw.js)
     - the horse list and the notes: IndexedDB, below
     - nothing in cookies or localStorage

   Rules this file keeps:
     - a note is never removed from the phone until the server has said it
       holds it (or the person taps Clear this phone)
     - every note has an id made on the phone, sent with every attempt, so
       the server stores it once
     - text from the server or the person goes on the page with
       textContent only, never as HTML
*/
(function () {
    'use strict';

    var core = window.CvetProtoCore;
    var config = document.body.dataset;
    var $ = function (id) { return document.getElementById(id); };

    var MAX_RECORDING_MS = 60 * 1000;
    var PHOTO_MAX_EDGE = 1600;
    var AUTO_SYNC_MS = 60 * 1000;

    var state = {
        horses: [],
        entries: [],
        meta: {},          // downloadedAt, userId, userName, lastSyncAt
        draft: null,       // the note being written
        syncing: false,
        recorder: null,
        storage: null,     // navigator.storage.estimate()
        persisted: null,   // navigator.storage.persisted()
        workerState: 'none',
        openMs: null       // from the tap on the icon to the horse list on the screen
    };

    // ── IndexedDB ────────────────────────────────────────────────────────

    var dbPromise = null;

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise(function (resolve, reject) {
            var request = indexedDB.open('cvet-prototype', 1);
            request.onupgradeneeded = function () {
                var db = request.result;
                db.createObjectStore('meta', { keyPath: 'key' });
                db.createObjectStore('horses', { keyPath: 'id' });
                db.createObjectStore('entries', { keyPath: 'clientId' });
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

    function setMeta(key, value) {
        state.meta[key] = value;
        return put('meta', { key: key, value: value });
    }

    function replaceHorses(horses) {
        return withStore('horses', 'readwrite', function (store) {
            store.clear();
            horses.forEach(function (horse) { store.put(horse); });
        });
    }

    function clearEverything() {
        return Promise.all(['meta', 'horses', 'entries'].map(function (name) {
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
        var settings = Object.assign({
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {}
        }, options || {});
        settings.headers = Object.assign({ Accept: 'application/json' }, settings.headers);
        if (controller) settings.signal = controller.signal;

        return fetch(url, settings).then(function (response) {
            var type = response.headers.get('Content-Type') || '';
            if (response.redirected || type.indexOf('application/json') === -1) {
                // Two-step sign-in and the sign-in page answer with HTML.
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

    // Who is signed in, and a CSRF token for the uploads.
    function getSession() {
        return request(config.sessionUrl, {}, 20000).then(function (result) {
            if (result.kind === 'ok' && result.status === 200) return { kind: 'ok', session: result.data };
            if (result.kind === 'ok') return { kind: 'signin' };  // 401 or 403
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

    // ── Rendering ────────────────────────────────────────────────────────

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function timeText(iso) {
        if (!iso) return 'never';
        var date = new Date(iso);
        return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) + ', ' +
            date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    }

    function renderSignal() {
        var chip = $('signal');
        chip.textContent = navigator.onLine ? 'Signal' : 'No signal';
        chip.className = navigator.onLine ? 'chip' : 'chip off';
    }

    function renderCounter() {
        var count = core.unsent(state.entries).length;
        var node = $('unsent');
        node.textContent = core.unsentText(count);
        node.className = 'chip count ' + (count ? 'pending' : 'clear');
        $('who').textContent = state.meta.userName ? 'Signed in as ' + state.meta.userName + '.' : '';
        $('downloaded').textContent = state.meta.downloadedAt
            ? state.horses.length + ' horses, downloaded ' + timeText(state.meta.downloadedAt) + '.'
            : '';
    }

    function renderHorses() {
        var list = $('horses');
        var query = $('search').value;
        // Built off the page and put on it in one step: one layout pass
        // however long the list is.
        var rows = document.createDocumentFragment();
        state.horses.filter(function (horse) {
            return core.matchesHorse(horse, query);
        }).forEach(function (horse) {
            var item = el('li');
            var button = el('button', 'horse');
            button.type = 'button';
            button.appendChild(el('span', 'strong', horse.name));
            var line = [horse.owner, horse.location].filter(Boolean).join(' · ');
            if (line) button.appendChild(el('small', '', line));
            // Chip and passport numbers take a line each horse. Show
            // them only while the vet is searching, to check a match.
            var ids = query ? [
                horse.microchip ? 'Chip ' + horse.microchip : '',
                horse.passport ? 'Passport ' + horse.passport : ''
            ].filter(Boolean).join(' · ') : '';
            if (ids) button.appendChild(el('small', '', ids));
            button.addEventListener('click', function () { startDraft(horse); });
            item.appendChild(button);
            rows.appendChild(item);
        });
        list.textContent = '';
        list.appendChild(rows);
        $('no-horses').hidden = state.horses.length > 0;
        $('search').hidden = state.horses.length === 0;
    }

    function renderEntries() {
        var list = $('entries');
        list.textContent = '';
        state.entries.slice().sort(function (a, b) {
            return a.createdAt < b.createdAt ? 1 : -1;
        }).forEach(function (entry) {
            var item = el('li', 'entry');
            item.appendChild(el('span', 'strong', entry.horseName || 'No horse'));
            var label = entry.status === 'sent' ? 'Sent ' + timeText(entry.sentAt)
                : entry.status === 'failed' ? 'Not sent: ' + (entry.lastError || 'the server refused it')
                : 'Not sent yet';
            item.appendChild(el('small', 'state ' + entry.status, label));
            if (entry.note) item.appendChild(el('small', '', entry.note));
            var parts = ['Saved ' + timeText(entry.createdAt)];
            if (entry.photoBytes) parts.push('photo ' + core.formatBytes(entry.photoBytes));
            if (entry.audioBytes) parts.push('voice ' + core.formatBytes(entry.audioBytes));
            if (entry.attempts > 1) parts.push(entry.attempts + ' tries');
            item.appendChild(el('small', '', parts.join(' · ')));
            list.appendChild(item);
        });
        $('no-entries').hidden = state.entries.length > 0;
    }

    function installed() {
        return window.navigator.standalone === true ||
            (typeof window.matchMedia === 'function' && window.matchMedia('(display-mode: standalone)').matches);
    }

    // What this phone can do. Sent with every note, so the results list
    // on the server shows it too.
    function clientInfo() {
        var canRecord = typeof window.MediaRecorder === 'function' &&
            !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
        return {
            version: config.version,
            userAgent: navigator.userAgent,
            standalone: installed(),
            workerState: state.workerState,
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

    function renderDetails() {
        var info = clientInfo();
        var rows = [
            ['App version', info.version],
            ['Installed on the home screen', info.standalone ? 'Yes' : 'No'],
            ['Opens with no signal', info.workerControls ? 'Yes' : 'Not yet: open the page once more with a signal'],
            ['Service worker', info.workerState],
            ['Sends while the app is closed', info.backgroundSync ? 'This browser can' : 'No: keep the app open to send'],
            ['Voice recording', info.canRecord ? 'Yes (' + (info.audioType || 'browser default') + ')' : 'No'],
            ['Opened in', info.openMs === null ? 'not known' : info.openMs + ' ms'],
            ['Storage used', core.formatBytes(info.storageUsed)],
            ['Storage allowed', core.formatBytes(info.storageQuota)],
            ['Storage protected from clean-up', info.storagePersisted === null ? 'not known' : info.storagePersisted ? 'Yes' : 'No'],
            ['Last sync', timeText(state.meta.lastSyncAt)],
            ['Browser', info.userAgent]
        ];
        var list = $('details');
        list.textContent = '';
        rows.forEach(function (row) {
            list.appendChild(el('dt', '', row[0]));
            list.appendChild(el('dd', '', String(row[1])));
        });
    }

    function render() {
        renderSignal();
        renderCounter();
        renderHorses();
        renderEntries();
        renderDetails();
    }

    function refreshStorage() {
        if (!navigator.storage || !navigator.storage.estimate) return Promise.resolve();
        return navigator.storage.estimate().then(function (estimate) {
            state.storage = estimate;
            return navigator.storage.persisted ? navigator.storage.persisted() : null;
        }).then(function (persisted) {
            state.persisted = persisted;
            renderDetails();
        }).catch(function () { /* details stay "not known" */ });
    }

    // Ask the browser not to clean this storage up. It may say no; the
    // details show the answer. Asked on a tap, as some browsers require.
    function askToKeepStorage() {
        if (!navigator.storage || !navigator.storage.persist) return;
        navigator.storage.persist().then(refreshStorage).catch(function () {});
    }

    function showSignIn(show) {
        $('sign-in').hidden = !show;
        $('sign-in-link').href = config.loginUrl;
    }

    // ── Download the horse list ──────────────────────────────────────────

    function download() {
        var button = $('download');
        button.disabled = true;
        $('sync-message').textContent = 'Downloading the horse list…';
        askToKeepStorage();
        return request(config.bootstrapUrl, {}, 30000).then(function (result) {
            if (result.kind === 'offline') {
                $('sync-message').textContent = 'No signal. The horse list on this phone has not changed.';
                return;
            }
            if (result.kind === 'signin' || result.status !== 200) {
                $('sync-message').textContent = 'Sign in again to download the horse list.';
                showSignIn(true);
                return;
            }
            var data = result.data;
            if (state.meta.userId && state.meta.userId !== data.user.id && core.unsent(state.entries).length) {
                $('sync-message').textContent = otherPersonText();
                return;
            }
            showSignIn(false);
            return replaceHorses(data.horses).then(function () {
                state.horses = data.horses;
                return setMeta('downloadedAt', data.generated_at);
            }).then(function () {
                return setMeta('userId', data.user.id);
            }).then(function () {
                return setMeta('userName', data.user.name);
            }).then(function () {
                $('sync-message').textContent = 'Horse list downloaded.';
            });
        }).catch(function (error) {
            $('sync-message').textContent = 'The phone could not store the horse list: ' + error.message;
        }).then(function () {
            button.disabled = false;
            render();
            refreshStorage();
        });
    }

    function otherPersonText() {
        return 'This phone holds unsent notes from ' + (state.meta.userName || 'another person') +
            '. Sign in as that person to send them.';
    }

    // ── Write a note ─────────────────────────────────────────────────────

    function startDraft(horse) {
        stopRecording();
        state.draft = { horse: horse, photo: null, audio: null };
        $('entry').hidden = false;
        $('entry-horse').textContent = horse.name;
        $('note').value = '';
        $('photo').value = '';
        $('photo-state').textContent = '';
        $('audio-state').textContent = '';
        $('entry-message').textContent = '';
        setPreview(null);
        $('entry').scrollIntoView({ behavior: 'smooth', block: 'start' });
        $('note').focus({ preventScroll: true });
    }

    function closeDraft() {
        stopRecording();
        state.draft = null;
        setPreview(null);
        $('entry').hidden = true;
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

    function saveDraft() {
        var draft = state.draft;
        if (!draft) return;
        if (state.recorder) {
            $('entry-message').textContent = 'Stop the recording first.';
            return;
        }
        var note = $('note').value.trim();
        if (!note && !draft.photo && !draft.audio) {
            $('entry-message').textContent = 'Type a note, take a photo or record your voice first.';
            return;
        }
        var entry = {
            clientId: core.newId(window.crypto),
            horseId: draft.horse.id,
            horseName: draft.horse.name,
            note: note,
            photo: draft.photo ? { buffer: draft.photo.buffer, type: draft.photo.type, sha256: draft.photo.sha256 } : null,
            photoBytes: draft.photo ? draft.photo.bytes : 0,
            audio: draft.audio ? { buffer: draft.audio.buffer, type: draft.audio.type, sha256: draft.audio.sha256 } : null,
            audioBytes: draft.audio ? draft.audio.bytes : 0,
            createdAt: new Date().toISOString(),
            status: 'pending',
            attempts: 0,
            sentAt: null,
            lastError: ''
        };
        $('save').disabled = true;
        askToKeepStorage();
        put('entries', entry).then(function () {
            state.entries.push(entry);
            closeDraft();
            render();
            refreshStorage();
            sync(false);
        }).catch(function (error) {
            // The note is still in the form: nothing is lost.
            $('entry-message').textContent = 'The phone could not save this note: ' + error.message;
        }).then(function () {
            $('save').disabled = false;
        });
    }

    // ── Sync ─────────────────────────────────────────────────────────────

    function sendEntry(entry, csrf) {
        var form = new FormData();
        form.append('client_id', entry.clientId);
        form.append('horse_id', String(entry.horseId));
        form.append('horse_name', entry.horseName);
        form.append('note', entry.note);
        form.append('device_created_at', entry.createdAt);
        form.append('attempts', String(entry.attempts));
        form.append('client_info', JSON.stringify(clientInfo()));
        if (entry.photo) {
            form.append('photo', new Blob([entry.photo.buffer], { type: entry.photo.type }), 'photo');
            form.append('photo_sha256', entry.photo.sha256);
        }
        if (entry.audio) {
            form.append('audio', new Blob([entry.audio.buffer], { type: entry.audio.type }), 'audio');
            form.append('audio_sha256', entry.audio.sha256);
        }
        return request(config.entriesUrl, {
            method: 'POST',
            body: form,
            headers: { 'X-CSRFToken': csrf }
        }, 120000);
    }

    // Send every unsent note, oldest first, one at a time. Stops at the
    // first note that cannot be sent now; the rest wait for the next sync.
    // `byHand` is true for the Sync button: it also sends notes the server
    // refused before, and it always says what happened.
    function sync(byHand) {
        if (state.syncing) return Promise.resolve();
        var queue = core.unsent(state.entries).filter(function (entry) {
            return byHand || entry.status !== 'failed';
        });
        var message = $('sync-message');
        if (!queue.length) {
            if (byHand) message.textContent = 'Nothing to send.';
            return Promise.resolve();
        }
        state.syncing = true;
        $('sync').disabled = true;
        if (byHand) message.textContent = 'Sending…';

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
                renderCounter();
                renderEntries();
                return next(csrf);
            });
        }

        return getSession().then(function (result) {
            if (result.kind !== 'ok') {
                stop = result.kind === 'signin' ? 'signin' : 'later';
                return;
            }
            var session = result.session;
            if (state.meta.userId && state.meta.userId !== session.user.id) {
                stop = 'other';
                return;
            }
            showSignIn(false);
            return next(session.csrf);
        }).catch(function (error) {
            stop = 'storage:' + error.message;
        }).then(function () {
            state.syncing = false;
            $('sync').disabled = false;
            var left = core.unsent(state.entries).length;
            if (stop === 'signin') {
                message.textContent = 'Sign in again to send. Your notes are safe on this phone.';
                showSignIn(true);
            } else if (stop === 'other') {
                message.textContent = otherPersonText();
            } else if (stop === 'later') {
                if (byHand || sent) {
                    message.textContent = (sent ? sent + ' sent. ' : '') +
                        'No signal for the rest. They stay on this phone and go when the signal is back.';
                }
            } else if (stop.indexOf('storage:') === 0) {
                message.textContent = 'The phone could not update its storage: ' + stop.slice(8);
            } else if (sent || byHand) {
                message.textContent = sent + (sent === 1 ? ' note sent.' : ' notes sent.') +
                    (left ? ' ' + left + ' could not be sent: see the list.' : '');
            }
            if (sent) setMeta('lastSyncAt', new Date().toISOString()).then(renderDetails);
            render();
            refreshStorage();
        });
    }

    // ── Clear ────────────────────────────────────────────────────────────

    function clearPhone() {
        var count = core.unsent(state.entries).length;
        var question = count
            ? core.unsentText(count) + '. Clearing deletes ' + (count === 1 ? 'it' : 'them') + ' for good. Clear this phone?'
            : 'Remove the horse list and all notes from this phone?';
        if (!window.confirm(question)) return;
        closeDraft();
        clearEverything().then(function () {
            state.horses = [];
            state.entries = [];
            state.meta = {};
            $('sync-message').textContent = 'This phone is clear.';
            render();
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
            renderDetails();
            navigator.serviceWorker.ready.then(function () {
                state.workerState = 'activated';
                renderDetails();
            });
        }).catch(function (error) {
            state.workerState = 'failed: ' + error.message;
            renderDetails();
        });
        navigator.serviceWorker.addEventListener('controllerchange', renderDetails);
    }

    function start() {
        $('sync').addEventListener('click', function () { sync(true); });
        $('download').addEventListener('click', download);
        $('search').addEventListener('input', renderHorses);
        $('photo').addEventListener('change', onPhotoChosen);
        $('record').addEventListener('click', toggleRecording);
        $('save').addEventListener('click', saveDraft);
        $('cancel').addEventListener('click', closeDraft);
        $('clear').addEventListener('click', clearPhone);

        window.addEventListener('online', function () { renderSignal(); sync(false); });
        window.addEventListener('offline', renderSignal);
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') sync(false);
        });
        // The phone cannot send while the app is closed (no background
        // sync on iPhone), so it tries each minute while the app is open.
        setInterval(function () { sync(false); }, AUTO_SYNC_MS);

        registerWorker();

        Promise.all([getAll('horses'), getAll('entries'), getAll('meta')]).then(function (stored) {
            state.horses = stored[0].sort(function (a, b) { return a.name.localeCompare(b.name); });
            state.entries = stored[1];
            stored[2].forEach(function (row) { state.meta[row.key] = row.value; });
            render();
            // The speed measure: time since the page started to load.
            // The plan's limit is 1 second (docs/C_VET_PLAN.md, section 5).
            if (window.performance && performance.now) {
                state.openMs = Math.round(performance.now());
                renderDetails();
            }
            refreshStorage();
            sync(false);
        }).catch(function (error) {
            $('sync-message').textContent = 'This browser cannot store data for the test: ' + error.message;
            render();
        });
    }

    if (!window.indexedDB) {
        $('sync-message').textContent = 'This browser cannot store data for the test.';
    } else {
        start();
    }
})();
